package com.coooolfan.xiaomialbumsyncer.service

import com.coooolfan.xiaomialbumsyncer.model.Crontab
import com.coooolfan.xiaomialbumsyncer.model.XiaomiAccount
import com.coooolfan.xiaomialbumsyncer.xiaomicloud.XiaoMiApi
import com.coooolfan.xiaomialbumsyncer.xiaomicloud.CloudResponseException
import org.babyfish.jimmer.sql.kt.KSqlClient
import com.fasterxml.jackson.databind.ObjectMapper
import org.noear.solon.annotation.Managed
import org.noear.solon.Solon
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.security.MessageDigest

data class MirrorRun(
    val id: String,
    val startedAt: String,
    val finishedAt: String? = null,
    val status: String,
)

/** XAS owns scheduling and task configuration; the bundled worker owns file reconciliation. */
@Managed
class MirrorService(private val mapper: ObjectMapper, private val api: XiaoMiApi, private val sql: KSqlClient) {
    private val processes = ConcurrentHashMap<Long, Process>()
    private val database: Path get() = Path.of(Solon.cfg().get("solon.app.db", "./db/xiaomialbumsyncer.db")).toAbsolutePath().normalize()
    private val state: Path get() = Path.of(System.getenv("MIRROR_STATE_DIR") ?: database.parent.resolve("mirror").toString()).toAbsolutePath().normalize()
    private fun task(id: Long): Path {
        require(id > 0)
        return state.resolve(id.toString())
    }
    private fun runDir(id: Long, run: String): Path {
        require(run.matches(Regex("[0-9a-f-]{36}")))
        return task(id).resolve("runs").resolve(run)
    }
    private fun save(path: Path, value: Any) {
        Files.createDirectories(path.parent)
        val temp = path.resolveSibling(path.fileName.toString() + ".tmp")
        mapper.writeValue(temp.toFile(), value)
        Files.move(temp, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }
    fun execute(crontab: Crontab) {
        val id = UUID.randomUUID().toString()
        val started = Instant.now().toString()
        val dir = runDir(crontab.id, id)
        save(dir.resolve("status.json"), MirrorRun(id, started, status = "running"))
        var status = "failed"
        try {
            require(crontab.config.syncMode == "MIRROR")
            val root = Path.of(mirrorTargetDirectory(crontab.config.targetPath)).toAbsolutePath().normalize()
            require(root.parent != null && !state.startsWith(root) && !root.startsWith(state))
            val selected = crontab.albums.map { it.remoteId.toString() }.filter { it != "-1" }
            val audio = crontab.config.downloadAudios && (crontab.config.mirrorAllAlbums || crontab.albums.any { it.remoteId == -1L })
            require(crontab.config.mirrorAllAlbums || selected.isNotEmpty() || audio) { "Select albums or enable all albums" }
            val userId = checkNotNull(sql.findById(XiaomiAccount::class, crontab.accountId)).userId
            val identity = "xiaomi:" + MessageDigest.getInstance("SHA-256").digest(userId.toByteArray(Charsets.UTF_8))
                .joinToString("") { "%02x".format(it) }
            val staging = (System.getenv("MIRROR_STAGING_BASE")?.let { Path.of(it).resolve(crontab.id.toString()) }
                ?: task(crontab.id).resolve("data/staging")).toAbsolutePath().normalize()
            val config = mapOf(
                "source" to identity, "account_id" to crontab.accountId,
                "staging_root" to staging.toString(),
                "root" to root.toString(), "state" to task(crontab.id).resolve("data").toString(),
                "run_dir" to dir.toString(), "apply" to !crontab.config.mirrorReportOnly,
                "album_ids" to if (crontab.config.mirrorAllAlbums) null else selected,
                "include_images" to crontab.config.downloadImages,
                "include_videos" to crontab.config.downloadVideos,
                "include_audio" to audio,
            )
            val python = System.getenv("MIRROR_PYTHON") ?: "/opt/mirror-venv/bin/python"
            val worker = System.getenv("MIRROR_WORKER") ?: "/app/mirror/worker.py"
            val process = ProcessBuilder(python, worker)
                .redirectError(ProcessBuilder.Redirect.DISCARD).start()
            processes[crontab.id] = process
            val timedOut = AtomicBoolean(false)
            val timer = Executors.newSingleThreadScheduledExecutor()
            timer.schedule({ timedOut.set(true); process.destroyForcibly() }, 48, TimeUnit.HOURS)
            try {
                val cloud = MirrorCloud(crontab, api) { process.isAlive }
                process.outputStream.bufferedWriter(Charsets.UTF_8).use { output ->
                    fun reply(value: Any) { output.write(mapper.writeValueAsString(value)); output.newLine(); output.flush() }
                    reply(config)
                    process.inputStream.bufferedReader(Charsets.UTF_8).useLines { requests ->
                        requests.forEach { line ->
                            val request = mapper.readTree(line)
                            val operation = request.path("operation").asText()
                            val response = try {
                                val result: Any = when (operation) {
                                    "snapshot" -> cloud.snapshot()
                                    "download" -> {
                                        val path = Path.of(request.path("path").asText()).toAbsolutePath().normalize()
                                        require(path.startsWith(staging) && path != staging)
                                        cloud.download(request.path("key").asText(), path)
                                        true
                                    }
                                    else -> error("Unknown worker operation")
                                }
                                mapOf("result" to result)
                            } catch (error: Exception) {
                                // Never pass cloud bodies, cookies, signed URLs or native exception texts to the UI.
                                val detail = if (error is CloudResponseException) error.message else error.javaClass.simpleName
                                mapOf("error" to "Native $operation failed: $detail; no cleanup committed")
                            }
                            if (process.isAlive) reply(response)
                        }
                    }
                }
                process.waitFor()
                if (timedOut.get()) status = "timed_out" else {
                    status = if (process.exitValue() == 0) {
                        if (crontab.config.mirrorReportOnly) "reported" else "completed"
                    } else if (process.exitValue() == 143 || process.exitValue() == 137) "cancelled" else "failed"
                }
            } catch (error: java.io.IOException) {
                // Destroying a worker can close its pipe while a native cloud response is in flight.
                if (process.waitFor(1, TimeUnit.SECONDS) && process.exitValue() in setOf(143, 137))
                    status = if (timedOut.get()) "timed_out" else "cancelled"
                else throw error
            } finally {
                timer.shutdownNow()
                if (process.isAlive) process.destroyForcibly().waitFor()
                processes.remove(crontab.id, process)
            }
        } finally {
            save(dir.resolve("status.json"), MirrorRun(id, started, Instant.now().toString(), status))
        }
    }
    fun stop(id: Long) { processes[id]?.destroy() }
    fun list(id: Long): List<MirrorRun> {
        val runs = task(id).resolve("runs")
        if (!Files.isDirectory(runs)) return emptyList()
        return Files.list(runs).use { paths ->
            paths.filter { Files.isRegularFile(it.resolve("status.json")) }
                .map { mapper.readValue(it.resolve("status.json").toFile(), MirrorRun::class.java) }
                .sorted(compareByDescending<MirrorRun> { it.startedAt }).limit(30).toList()
                .map { if (it.status == "running" && !processes.containsKey(id)) it.copy(status = "interrupted") else it }
        }
    }
    fun report(id: Long, run: String): String {
        val dir = runDir(id, run)
        val path = if (Files.exists(dir.resolve("report.json"))) dir.resolve("report.json") else dir.resolve("progress.json")
        return if (Files.exists(path)) Files.readString(path) else "{}"
    }

    private fun resource(id: Long, run: String, category: String, index: Int): Pair<Path?, Boolean> {
        val crontab = checkNotNull(sql.findById(Crontab::class, id))
        return MirrorFiles(mapper, task(id).resolve("data")).resolve(
            mapper.readTree(report(id, run)), category, index, Path.of(mirrorTargetDirectory(crontab.config.targetPath)))
    }

    fun fileInfo(id: Long, run: String, category: String, index: Int): Map<String, Any> {
        val (path, isolated) = resource(id, run, category, index)
        val configured = mapper.readTree(System.getenv("MIRROR_HOST_PATH_MAP") ?: "{}")
        val mappings = configured.properties().associate { it.key to it.value.asText() }
        return mapOf("exists" to (path != null && Files.isRegularFile(path)),
            "path" to (path?.let { mirrorHostPath(it, mappings) } ?: ""),
            "folder" to (path?.parent?.let { mirrorHostPath(it, mappings) } ?: ""),
            "location" to if (isolated) "隔离副本" else "当前文件")
    }

    fun preview(id: Long, run: String, category: String, index: Int): ByteArray? =
        resource(id, run, category, index).first?.let { path ->
            if (path.fileName.toString().substringAfterLast('.', "").lowercase() !in setOf("jpg", "jpeg", "png", "gif", "bmp")) null
            else try { mirrorPreview(path) } catch (_: java.io.IOException) { null }
        }
}
