package com.coooolfan.xiaomialbumsyncer.service

import com.coooolfan.xiaomialbumsyncer.model.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.nio.file.Path
import java.time.Instant

class MirrorCloudTest {
    private fun album(remote: Long, count: Long = 1) = Album {
        remoteId = remote; name = if (remote == -1L) "录音" else "相机"
        assetCount = count; lastUpdateTime = Instant.EPOCH; accountId = 2
    }
    private fun asset(type: AssetType = AssetType.IMAGE) = Asset {
        id = 42; fileName = "sample.jpg"; this.type = type; sha1 = "a".repeat(40); size = 7
    }
    private fun task(images: Boolean = true, all: Boolean = true) = Crontab {
        id = 2; accountId = 2; albums = emptyList()
        config = CrontabConfig("0 0 3 * * ?", "Asia/Shanghai", "/photos", images, true, false, rewriteExifTimeZone = null,
            syncMode = "MIRROR", mirrorAllAlbums = all)
    }
    @Test fun `native assets keep existing baseline keys and recording paths`() {
        var downloaded: Asset? = null
        val cloud = MirrorCloud(task(), { listOf(album(1), album(-1, 0)) }, { album, handler ->
            assertEquals(album.remoteId, album.id)
            handler(listOf(asset(if (album.remoteId == -1L) AssetType.AUDIO else AssetType.IMAGE))); 1
        }, { item, _ -> downloaded = item; true })
        val snapshot = cloud.snapshot()
        assertEquals(setOf("photo:1:42", "audio:42"), snapshot.keys)
        assertEquals("录音/42_sample.jpg", snapshot["audio:42"]?.get("path"))
        cloud.download("audio:42", Path.of("stage"))
        assertEquals(AssetType.AUDIO, downloaded?.type)
    }
    @Test fun `count mismatch duplicate pages missing camera and changing albums block cleanup`() {
        for (mode in listOf("count", "duplicate", "camera", "change")) {
            var calls = 0
            val cloud = MirrorCloud(task(), {
                calls++
                listOf(album(if (mode == "camera") 2 else 1, if (mode == "count" || mode == "change" && calls > 1) 2 else 1))
            }, { _, handler -> handler(listOf(asset())); if (mode == "duplicate") handler(listOf(asset())); 1 }, { _, _ -> true })
            assertThrows(IllegalStateException::class.java) { cloud.snapshot() }
        }
    }
    @Test fun `filters count all raw assets and unavailable downloads fail closed`() {
        val cloud = MirrorCloud(task(images = false), { listOf(album(1)) }, { _, handler -> handler(listOf(asset())); 1 }, { _, _ -> true })
        assertTrue(cloud.snapshot().isEmpty())
        val unavailable = MirrorCloud(task(), { listOf(album(1)) }, { _, handler -> handler(listOf(asset())); 1 }, { _, _ -> false })
        unavailable.snapshot()
        assertThrows(IllegalStateException::class.java) { unavailable.download("photo:1:42", Path.of("stage")) }
    }
}
