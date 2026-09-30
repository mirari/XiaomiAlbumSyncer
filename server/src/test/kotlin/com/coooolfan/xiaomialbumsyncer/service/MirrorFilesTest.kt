package com.coooolfan.xiaomialbumsyncer.service

import com.fasterxml.jackson.databind.ObjectMapper
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.awt.image.BufferedImage
import java.io.ByteArrayInputStream
import java.nio.file.Files
import java.nio.file.Path
import javax.imageio.ImageIO

class MirrorFilesTest {
    @Test fun `upstream template and legacy directory resolve to the same mirror root`() {
        val root = "/photos/person"
        val template = root + "/" + com.coooolfan.xiaomialbumsyncer.model.CrontabHistoryDetail.DEFAULT_FILE_TEMPLATE
        assertEquals(root, mirrorTargetDirectory(template))
        assertEquals(root, mirrorTargetDirectory(root))
        assertThrows(IllegalArgumentException::class.java) {
            mirrorTargetDirectory(root + "/" + "$" + "{year}/" + "$" + "{fileName}")
        }
    }

    @TempDir lateinit var temp: Path
    private val mapper = ObjectMapper()

    @Test fun `deleted previews use the exact quarantine run including legacy reports`() {
        val state = temp.resolve("state")
        Files.createDirectories(state.resolve("reports"))
        val report = mapper.readTree("""{"time":123.5,"deleted":["album/photo.jpg"],"quarantined":["album/photo.jpg"]}""")
        Files.writeString(state.resolve("reports/123-abcdef12.json"), report.toString())
        Files.writeString(state.resolve("reports/124-abcdef12.json"), report.toString().replace("123.5", "124.5"))
        val resolver = MirrorFiles(mapper, state)
        val expected = state.resolve("quarantine/123-abcdef12/album/photo.jpg")
        assertEquals(expected to true, resolver.resolve(report, "deleted", 0, temp.resolve("photos")))
        (report as com.fasterxml.jackson.databind.node.ObjectNode).put("quarantine_run", "123-abcdef12")
        assertEquals(expected to true, resolver.resolve(report, "quarantined", 0, temp.resolve("photos")))
    }

    @Test fun `only listed files within task roots can be resolved`() {
        val root = Files.createDirectory(temp.resolve("photos"))
        val resolver = MirrorFiles(mapper, temp.resolve("state"))
        for (relative in listOf("../secret", "/secret", "album/../../secret", "C:/secret", "album\\secret")) {
            val report = mapper.valueToTree<com.fasterxml.jackson.databind.JsonNode>(mapOf("added" to listOf(relative)))
            assertThrows(IllegalArgumentException::class.java) { resolver.resolve(report, "added", 0, root) }
        }
        val report = mapper.readTree("""{"added":["album/photo.jpg"]}""")
        assertThrows(IllegalArgumentException::class.java) { resolver.resolve(report, "added", 1, root) }
        assertThrows(IllegalArgumentException::class.java) { resolver.resolve(report, "secret", 0, root) }
        Files.createSymbolicLink(root.resolve("album"), temp)
        assertThrows(IllegalArgumentException::class.java) { resolver.resolve(report, "added", 0, root) }
    }

    @Test fun `host path mapping uses the longest directory prefix`() {
        val mappings = mapOf(temp.toString() to "D:\\photos", temp.resolve("quarantine").toString() to "C:\\quarantine")
        assertEquals("C:\\quarantine\\run\\photo.jpg", mirrorHostPath(temp.resolve("quarantine/run/photo.jpg"), mappings))
        assertEquals("D:\\photos\\album\\photo.jpg", mirrorHostPath(temp.resolve("album/photo.jpg"), mappings))
    }

    @Test fun `preview is bounded jpeg and unsupported content has no preview`() {
        val original = temp.resolve("photo.png")
        ImageIO.write(BufferedImage(1600, 800, BufferedImage.TYPE_INT_RGB), "png", original.toFile())
        val bytes = mirrorPreview(original)
        assertNotNull(bytes)
        val thumb = ImageIO.read(ByteArrayInputStream(bytes!!))
        assertEquals(640, thumb.width)
        assertEquals(320, thumb.height)
        val unsupported = Files.writeString(temp.resolve("video.mp4"), "not an image")
        assertNull(mirrorPreview(unsupported))
    }
}
