package com.coooolfan.xiaomialbumsyncer.xiaomicloud

import com.fasterxml.jackson.databind.ObjectMapper
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class CloudResponseTest {
    private val mapper = ObjectMapper()
    @Test fun `transient business errors retry with bounded backoff`() {
        var calls = 0
        val waits = mutableListOf<Long>()
        val result = readCloudPage({ waits.add(it) }) {
            mapper.readTree(if (++calls < 4) """{"code":10001,"retriable":true}""" else """{"code":0,"data":{}}""")
        }
        assertEquals(0, result.path("code").asInt())
        assertEquals(listOf(2000L, 4000L, 8000L), waits)
        calls = 0
        assertThrows(CloudResponseException::class.java) {
            readCloudPage({}) { calls++; mapper.readTree("""{"code":10001,"retriable":true}""") }
        }
        assertEquals(4, calls)
    }
    @Test fun `failed malformed and missing pages never become empty collections`() {
        for (json in listOf("{}", """{"code":0,"data":null}""", """{"code":401,"data":{}}""")) {
            assertThrows(CloudResponseException::class.java) { readCloudPage { mapper.readTree(json) } }
        }
        for (json in listOf("""{"data":{"albums":[]}}""", """{"data":{"albums":[],"isLastPage":false}}""")) {
            assertThrows(CloudResponseException::class.java) { requireCloudPage(mapper.readTree(json), "albums", true) }
        }
        val empty = mapper.readTree("""{"data":{"isLastPage":true}}""")
        requireCloudPage(empty, "galleries", true, allowEmpty = true)
        assertThrows(CloudResponseException::class.java) { requireCloudPage(empty, "galleries", true) }
    }
}
