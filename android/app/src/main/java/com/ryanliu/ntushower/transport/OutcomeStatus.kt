package com.ryanliu.ntushower.transport

/**
 * 命令结果状态（协议 `device.command_result.status` 的取值）。
 *
 * **本文件由 tools/sync_contracts.py 生成，不要手改**——改了会在下一轮生成时被覆盖，
 * 而 `tests/test_contract_sync.py` 也会立刻失败。要动取值，改服务端的
 * `app/core/outcomes.py` 后重新生成。
 *
 * 四者的分界（判断该回哪个时照这个来）：
 * - [OK]        做完了
 * - [FAILED]    动手了但没成
 * - [TIMEOUT]   超过时限没有结论，**不知道成没成**
 * - [REJECTED]  根本没动手，没有副作用，**重试是安全的**
 */
object OutcomeStatus {
    /** 成功 */
    const val OK = "ok"

    /** 失败 */
    const val FAILED = "failed"

    /** 超时 */
    const val TIMEOUT = "timeout"

    /** 被拒 */
    const val REJECTED = "rejected"

    /** 全部取值。 */
    val ALL: Set<String> = setOf(
        OK,
        FAILED,
        TIMEOUT,
        REJECTED,
    )
}
