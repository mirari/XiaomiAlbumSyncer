package com.coooolfan.xiaomialbumsyncer.service

import com.coooolfan.xiaomialbumsyncer.model.*
import com.coooolfan.xiaomialbumsyncer.xiaomicloud.XiaoMiApi
import java.nio.file.Path

/** Snapshot constraints for destructive reconciliation, using the upstream API throughout. */
internal class MirrorCloud(
    private val crontab: Crontab,
    private val albums: () -> List<Album>,
    private val assets: (Album, (List<Asset>) -> Unit) -> Long,
    private val download: (Asset, Path) -> Boolean,
) {
    constructor(crontab: Crontab, api: XiaoMiApi, active: () -> Boolean) : this(
        crontab, { check(active()); api.fetchAllAlbums(crontab.accountId).also { check(active()) } },
        { album, handler ->
            check(active())
            api.fetchAssetsByAlbumId(album) { page -> check(active()); handler(page) }
        },
        { asset, path -> check(active()); api.downloadAsset(crontab.accountId, asset, path) },
    )

    private val scanned = mutableMapOf<String, Asset>()

    fun snapshot(): Map<String, Map<String, Any>> {
        scanned.clear()
        val before = albums()
        val photoAlbums = before.filter { it.remoteKey != "-1" }
        check(photoAlbums.any { it.remoteKey == "1" }) { "Camera album missing; cleanup refused" }
        check(before.map { it.remoteKey }.distinct().size == before.size) { "Duplicate album page" }
        val selected = crontab.albums.map { it.remoteKey }.toSet()
        check(crontab.config.mirrorAllAlbums || before.map { it.remoteKey }.containsAll(selected)) {
            "Selected album missing; cleanup refused"
        }
        val entries = linkedMapOf<String, Map<String, Any>>()
        for (remote in before) {
            if (!crontab.config.mirrorAllAlbums && remote.remoteKey !in selected) continue
            val audio = remote.remoteKey == "-1"
            if (audio && !crontab.config.downloadAudios) continue
            // Native parsing needs a local album id; this snapshot is never persisted to SQL.
            val album = Album(remote) { id = remote.remoteKey.toLong() }
            val seen = mutableSetOf<String>()
            assets(album) { page ->
                for (asset in page) {
                    check(seen.add(asset.remoteKey)) { "Duplicate asset page; cleanup refused" }
                    if (asset.type == AssetType.IMAGE && !crontab.config.downloadImages) continue
                    if (asset.type == AssetType.VIDEO && !crontab.config.downloadVideos) continue
                    val key = if (audio) "audio:${asset.remoteKey}" else "photo:${album.remoteKey}:${asset.remoteKey}"
                    val name = if (audio) "${asset.remoteKey}_${asset.fileName}" else asset.fileName
                    for (component in listOf(album.name, name)) {
                        require(component.isNotEmpty() && component.none { it == '/' || it == '\\' }) {
                            "Cloud names must be single path components"
                        }
                    }
                    entries[key] = mapOf("path" to "${album.name}/$name", "sha1" to asset.checksum.lowercase(), "size" to asset.size)
                    // Snapshot assets are not persisted; the upstream downloader uses id in error logs.
                    scanned[key] = Asset(asset) { id = asset.remoteKey.toLong() }
                }
            }
            check(audio || seen.size.toLong() == album.assetCount) { "Album count changed or scan incomplete; cleanup refused" }
        }
        fun signature(list: List<Album>) = list.filter { it.remoteKey != "-1" }
            .associate { it.remoteKey to Triple(it.name, it.assetCount, it.lastUpdateTime) }
        check(signature(before) == signature(albums())) { "Albums changed during scan; retry later" }
        return entries
    }

    fun download(key: String, path: Path) {
        check(download(checkNotNull(scanned[key]), path)) { "Cloud file unavailable; cleanup refused" }
    }
}
