package com.coooolfan.xiaomialbumsyncer.xiaomicloud

import com.fasterxml.jackson.databind.JsonNode

/** Sanitized failure: a failed cloud page must never be interpreted as an empty collection. */
class CloudResponseException(message: String) : IllegalStateException(message)

internal fun readCloudPage(
    pause: (Long) -> Unit = { Thread.sleep(it) },
    read: () -> JsonNode,
): JsonNode {
    repeat(4) { attempt ->
        val tree = read()
        val code = tree.path("code")
        if (code.isIntegralNumber && code.asInt() == 0 && tree.path("data").isObject) return tree
        if (!tree.path("retriable").asBoolean() || attempt == 3) {
            throw CloudResponseException("Cloud page rejected (code=${if (code.isIntegralNumber) code.asInt() else "missing"})")
        }
        pause(2000L shl attempt)
    }
    error("Unreachable")
}

internal fun requireCloudPage(tree: JsonNode, array: String, paginated: Boolean, allowEmpty: Boolean = false) {
    val values = tree.at("/data/$array")
    if ((!values.isArray && !(allowEmpty && values.isMissingNode)) ||
        (paginated && !tree.at("/data/isLastPage").isBoolean) ||
        (paginated && values.size() == 0 && !tree.at("/data/isLastPage").asBoolean())) {
        throw CloudResponseException("Cloud page incomplete")
    }
}
