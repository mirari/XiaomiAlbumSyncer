package com.coooolfan.xiaomialbumsyncer.service

import com.coooolfan.xiaomialbumsyncer.model.Crontab
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

data class MirrorRun(
    val id: String,
    val startedAt: String,
    val finishedAt: String? = null,
    val status: String,
)

/** XAS owns scheduling and task configuration; the bundled worker owns file reconciliation. */
@Managed
class MirrorService(private val mapper: ObjectMapper) {
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
            require(crontab.config.expressionTargetPath.isBlank()) { "Mirror mode requires an ordinary target directory" }
            val root = Path.of(crontab.config.targetPath).toAbsolutePath().normalize()
            require(root.parent != null && !state.startsWith(root) && !root.startsWith(state))
            val selected = crontab.albums.map { it.remoteId.toString() }.filter { it != "-1" }
            val audio = crontab.config.downloadAudios && (crontab.config.mirrorAllAlbums || crontab.albums.any { it.remoteId == -1L })
            require(crontab.config.mirrorAllAlbums || selected.isNotEmpty() || audio) { "Select albums or enable all albums" }
            val config = mapOf(
                "database" to database.toString(), "account_id" to crontab.accountId,
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
                .redirectOutput(ProcessBuilder.Redirect.DISCARD)
                .redirectError(ProcessBuilder.Redirect.DISCARD).start()
            processes[crontab.id] = process
            try {
                process.outputStream.use { mapper.writeValue(it, config) }
                if (!process.waitFor(48, TimeUnit.HOURS)) {
                    process.destroyForcibly().waitFor()
                    status = "timed_out"
                } else {
                    status = if (process.exitValue() == 0) {
                        if (crontab.config.mirrorReportOnly) "reported" else "completed"
                    } else if (process.exitValue() == 143 || process.exitValue() == 137) "cancelled" else "failed"
                }
            } finally {
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
}
