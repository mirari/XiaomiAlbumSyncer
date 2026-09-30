package com.coooolfan.xiaomialbumsyncer.service

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import java.awt.RenderingHints
import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.nio.file.Path
import javax.imageio.ImageIO
import kotlin.math.max

/** Resolve only entries recorded in a task report, never an arbitrary client-supplied path. */
internal class MirrorFiles(private val mapper: ObjectMapper, private val state: Path) {
    fun resolve(report: JsonNode, category: String, index: Int, root: Path): Pair<Path?, Boolean> {
        require(category in setOf("added", "modified", "moved", "deleted", "quarantined"))
        val entries = report.path(category)
        require(entries.isArray && index >= 0 && index < entries.size())
        val entry = entries[index]
        val relative = if (entry.isTextual) entry.asText() else entry.path("to").asText()
        val isolated = category == "quarantined" || category == "deleted" &&
            report.path("quarantined").any { it.asText() == relative }
        if (!isolated) return contained(root, relative) to false
        // Older reports identify the quarantine run through the matching local journal.
        val run = report.path("quarantine_run").asText().ifBlank {
            val journals = state.resolve("reports")
            if (!Files.isDirectory(journals)) return null to true
            Files.list(journals).use { files ->
                files.filter { it.fileName.toString().matches(Regex("[0-9]+-[0-9a-f]{8}\\.json")) }
                    .filter {
                        val old = mapper.readTree(it.toFile())
                        old.path("time") == report.path("time") && old.path("quarantined").any { item -> item.asText() == relative }
                    }.map { it.fileName.toString().removeSuffix(".json") }.findFirst().orElse("")
            }
        }
        if (!run.matches(Regex("[0-9]+-[0-9a-f]{8}"))) return null to true
        return contained(state.resolve("quarantine"), "$run/$relative") to true
    }

    private fun contained(root: Path, relative: String): Path {
        require(relative.isNotBlank() && '\\' !in relative)
        val parts = relative.split('/')
        require(parts.none { it.isBlank() || it == "." || it == ".." || ':' in it })
        val base = root.toAbsolutePath().normalize()
        val file = base.resolve(relative).normalize()
        require(file.startsWith(base) && file != base)
        var path = base
        for (part in parts) {
            path = path.resolve(part)
            require(!Files.isSymbolicLink(path))
        }
        if (Files.exists(file)) require(file.toRealPath().startsWith(base.toRealPath()))
        return file
    }
}

internal fun mirrorHostPath(path: Path, mappings: Map<String, String>): String {
    val absolute = path.toAbsolutePath().normalize()
    val prefix = mappings.keys.sortedByDescending { it.length }.firstOrNull { absolute.startsWith(Path.of(it)) }
        ?: return absolute.toString()
    val suffix = Path.of(prefix).relativize(absolute).toString().replace('\\', '/')
    val host = mappings.getValue(prefix).trimEnd('/', '\\')
    val separator = if (host.contains('\\') || Regex("^[A-Za-z]:").containsMatchIn(host)) "\\" else "/"
    return host + if (suffix.isEmpty()) "" else separator + suffix.replace("/", separator)
}

internal fun mirrorPreview(path: Path): ByteArray? {
    if (!Files.isRegularFile(path)) return null
    ImageIO.createImageInputStream(path.toFile()).use { input ->
        if (input == null) return null
        val readers = ImageIO.getImageReaders(input)
        if (!readers.hasNext()) return null
        val reader = readers.next()
        try {
            reader.input = input
            val width = reader.getWidth(0)
            val height = reader.getHeight(0)
            if (width <= 0 || height <= 0 || width.toLong() * height > 200_000_000L) return null
            val options = reader.defaultReadParam
            val sample = max(1, max(width, height) / 640)
            options.setSourceSubsampling(sample, sample, 0, 0)
            val source = reader.read(0, options)
            val scale = minOf(1.0, 640.0 / max(source.width, source.height))
            val thumb = BufferedImage(max(1, (source.width * scale).toInt()), max(1, (source.height * scale).toInt()), BufferedImage.TYPE_INT_RGB)
            val graphics = thumb.createGraphics()
            try {
                graphics.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR)
                graphics.drawImage(source, 0, 0, thumb.width, thumb.height, null)
            } finally { graphics.dispose() }
            return ByteArrayOutputStream().use { output -> ImageIO.write(thumb, "jpg", output); output.toByteArray() }
        } finally { reader.dispose() }
    }
}

/** Accept upstream's default file template while keeping existing mirror baseline roots. */
internal fun mirrorTargetDirectory(targetPath: String): String {
    val suffix = "/" + com.coooolfan.xiaomialbumsyncer.model.CrontabHistoryDetail.DEFAULT_FILE_TEMPLATE
    val directory = targetPath.trim().removeSuffix(suffix)
    require(directory.isNotBlank() && !directory.contains("$" + "{")) {
        "Mirror mode supports a plain directory or the default album/downloadFileName template"
    }
    return directory
}
