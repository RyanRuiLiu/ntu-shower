package com.ryanliu.ntushower.transport

/**
 * OTA 版本号的形状：服务端发布时铸造、拼进文件路径，设备端下载前按它校验
 * manifest 的 `version` 字段（版本号参与文件名构造，先校验形状）。
 *
 * **本文件由 tools/sync_contracts.py 生成，不要手改**——改了会在下一轮生成时被
 * 覆盖，而 `tests/test_contract_sync.py` 也会立刻失败。要动版本号形状，改服务端的
 * `app/modules/ota/service.py` 后重新生成。
 */
object OtaContract {
    const val VERSION_PATTERN = "^\\d+\\.\\d+\\.\\d+$"
}
