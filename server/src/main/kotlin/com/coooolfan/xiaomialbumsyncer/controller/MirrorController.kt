package com.coooolfan.xiaomialbumsyncer.controller

import cn.dev33.satoken.annotation.SaCheckLogin
import com.coooolfan.xiaomialbumsyncer.service.MirrorService
import com.coooolfan.xiaomialbumsyncer.service.MirrorRun
import org.noear.solon.annotation.*
import org.noear.solon.core.handle.MethodType

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
}
