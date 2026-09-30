package com.coooolfan.xiaomialbumsyncer.controller

import cn.dev33.satoken.annotation.SaCheckLogin
import com.coooolfan.xiaomialbumsyncer.service.MirrorService
import com.coooolfan.xiaomialbumsyncer.service.MirrorRun
import org.noear.solon.annotation.*
import org.noear.solon.core.handle.MethodType
import org.noear.solon.core.handle.Context

@Managed
@Controller
@SaCheckLogin
@Mapping("/api/crontab/{id}/mirror")
class MirrorController(private val service: MirrorService) {
    @Mapping("/runs", method = [MethodType.GET])
    fun runs(@Path id: Long): List<MirrorRun> = service.list(id)

    @Mapping("/runs/{run}", method = [MethodType.GET])
    fun report(@Path id: Long, @Path run: String): Map<String, String> = mapOf("report" to service.report(id, run))

    @Mapping("/stop", method = [MethodType.POST])
    fun stop(@Path id: Long) { service.stop(id) }

    @Mapping("/runs/{run}/files/{category}/{index}", method = [MethodType.GET])
    fun file(@Path id: Long, @Path run: String, @Path category: String, @Path index: Int): Map<String, Any> =
        service.fileInfo(id, run, category, index)

    @Mapping("/runs/{run}/files/{category}/{index}/preview", method = [MethodType.GET])
    fun preview(@Path id: Long, @Path run: String, @Path category: String, @Path index: Int, ctx: Context) {
        ctx.headerSet("Cache-Control", "private, no-store")
        ctx.headerSet("X-Content-Type-Options", "nosniff")
        val image = service.preview(id, run, category, index)
        if (image == null) { ctx.status(404); return }
        ctx.contentType("image/jpeg")
        ctx.output(image)
    }
}
