package com.coooolfan.xiaomialbumsyncer.model

import org.babyfish.jimmer.sql.Serialized

/**
 * 单个相册的同步位点
 *
 * @param syncTag /gallery/allitems 返回的续拉位点，原样持久化
 * @param incrementalTag 追平后写入的本轮相册水位头（/gallery/album/full 的 incrementalTag）。
 * 拉取中途为 null，表示该相册尚未追平、下次运行需从此 syncTag 续拉
 */
@Serialized
data class AlbumSyncCursor(
    val syncTag: String,
    val incrementalTag: String?,
)
