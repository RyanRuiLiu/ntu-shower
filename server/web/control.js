/**
 * 遥控视图：实时画面 + 远程操作。
 *
 * 布局与视觉取自旧系统的 bridge-server/src/viewer.html。旧版通过一条常驻
 * ADB 隧道取画面；本系统经 `/admin/devices/{id}/screen` 取（设备端截屏后
 * 以 base64 回传），因此需要管理令牌，且是**按需拉取**而非持续推流。
 *
 * 界面节点树暂未接入：它需要设备端导出节点树的接口，属于"功能后续加"。
 */

import { $, on, store, toast } from './app.js'

const TOKEN_KEY = 'ntu-shower.admin-token'
/** 画面刷新间隔。设备截屏本身有耗时，太快只会积压请求。 */
const SCREEN_INTERVAL_MS = 1500

let timer = null
let inFlight = false

function getToken() {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

function setToken(value) {
  if (value) {
    localStorage.setItem(TOKEN_KEY, value)
  } else {
    localStorage.removeItem(TOKEN_KEY)
  }
  updateChip()
}

function updateChip() {
  const chip = $('btnToken')
  chip.textContent = getToken() ? '令牌已设置' : '管理令牌'
}

function setScreenState(state, text) {
  const chip = $('screenChip')
  chip.className = `bar-chip ${state}`
  chip.textContent = text
}

/**
 * 拉一帧画面。
 *
 * 用 `inFlight` 防重入：设备截屏要几百毫秒，而上一次的结果没回来就发下一次
 * 会让请求堆积，设备端也会被反复打断。
 */
async function fetchFrame() {
  if (inFlight || !store.deviceId) return

  const token = getToken()
  if (!token) {
    stopScreen()
    setScreenState('err', '令牌缺失')
    toast('请先设置管理令牌')
    return
  }

  inFlight = true
  try {
    const res = await fetch(`/admin/devices/${store.deviceId}/screen`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Token': token },
    })

    if (!res.ok) {
      const problem = await res.json().catch(() => null)
      throw new Error(problem?.detail || problem?.title || `HTTP ${res.status}`)
    }

    const body = await res.json()
    const image = body?.data?.image_base64
    if (image) {
      $('screenImg').src = `data:image/jpeg;base64,${image}`
      $('screenImg').hidden = false
      $('screenPlaceholder').hidden = true
      setScreenState('ok', '画面中')
    }
  } catch (err) {
    setScreenState('err', err.message)
    // 连续失败无意义，停掉让用户自己重试
    stopScreen()
  } finally {
    inFlight = false
  }
}

function startScreen() {
  if (timer) return
  if (!store.deviceId) {
    toast('尚未获取到设备')
    return
  }
  $('btnScreenOn').disabled = true
  $('btnScreenOff').disabled = false
  setScreenState('warn', '连接中')
  fetchFrame()
  timer = setInterval(fetchFrame, SCREEN_INTERVAL_MS)
}

function stopScreen() {
  clearInterval(timer)
  timer = null
  $('btnScreenOn').disabled = false
  $('btnScreenOff').disabled = true
}

/** 远程按键。设备端支持 app.restart 等命令，导航键暂未接入设备端。 */
function sendKey(action) {
  toast(`「${action}」尚未接入设备端`)
}

export function mountControl() {
  $('btnScreenOn').addEventListener('click', startScreen)
  $('btnScreenOff').addEventListener('click', () => {
    stopScreen()
    setScreenState('err', '已停止')
  })

  $('btnToken').addEventListener('click', () => {
    const value = prompt('管理令牌（与服务器 .env 的 ADMIN_TOKEN 一致）', getToken())
    if (value === null) return
    setToken(value.trim())
    toast(getToken() ? '令牌已保存' : '令牌已清除')
  })

  for (const btn of document.querySelectorAll('[data-act]')) {
    btn.addEventListener('click', () => sendKey(btn.textContent))
  }

  // 切走视图时停止拉画面：后台继续截屏会白白占用设备
  on('view', (name) => {
    if (name !== 'control') {
      stopScreen()
      setScreenState('err', '画面')
    }
  })

  updateChip()
}
