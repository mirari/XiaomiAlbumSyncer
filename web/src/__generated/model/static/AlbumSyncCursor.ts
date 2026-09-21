/**
 * 单个相册的同步位点
 * 
 */
export interface AlbumSyncCursor {
    readonly syncTag: string;
    readonly incrementalTag?: string | undefined;
}
