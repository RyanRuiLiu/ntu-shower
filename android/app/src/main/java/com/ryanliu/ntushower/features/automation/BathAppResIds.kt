package com.ryanliu.ntushower.features.automation

/**
 * 浴室 App 的控件资源 ID —— 只在这里写一份，别处只引用。
 *
 * 这些字符串是与浴室 App 之间的**契约**：它换了 ID，我们全部的定位与判定一起失效，而失效
 * 表现得像"界面卡住"，不像报错。集中一份，是为了改的时候只有一个地方要看，也让"我们依赖它
 * 哪些控件"有一个一眼看完的答案。
 *
 * 常量名取语义，不取它原生的 `tv_` / `tvb_` / `et_` 前缀——那几个前缀在它自己那边就是混着
 * 用的，照抄进来只会把那份混乱引进我们的命名。原始 resId 写在每个常量的注释里，排障时拿
 * 节点树里的一行能直接对上。
 *
 * 单测里刻意**另写一份字面量**，不引用这里（见 `BathAppPageTest`）：常量本身写错时，跟着它
 * 一起错的测试什么都发现不了。
 */
internal object BathAppResIds {

    // ── 首页 ──

    /**
     * 喷头网格（`gv_sprinkler`）。
     *
     * 判定首页的依据，也是定位单个喷头的起点。首页是全流程的稳定态，这一条被查得最频繁。
     */
    const val SPRINKLER_GRID = "com.datalook.bath:id/gv_sprinkler"

    /** 喷头项里的标题控件（`tv_title`）。它的文本是喷头编号，用它认喷头。 */
    const val DOOR_TITLE = "com.datalook.bath:id/tv_title"

    /** 首页底部的无卡模式按钮（`tv_button_nocard`）。分配流程点它。 */
    const val NOCARD_BUTTON = "com.datalook.bath:id/tv_button_nocard"

    /** 首页底部的结算按钮（`tv_button_nocard_strike`）。结算流程点它。 */
    const val SETTLE_BUTTON = "com.datalook.bath:id/tv_button_nocard_strike"

    /**
     * 首页的日期显示（`tv_nowdate`）。
     *
     * 只用来取一个坐标：收尾时往它身上注入一次真实触摸，清掉喷头网格的选中态。无障碍的点击
     * 清不掉那种选中态，必须走 `input tap`。
     */
    const val DATE_VIEW = "com.datalook.bath:id/tv_nowdate"

    // ── 返回 ──

    /** 各二级页左上角的"返回上一页"（`tv_backprepage`）。回到首页靠反复点它。 */
    const val BACK_TO_PREVIOUS = "com.datalook.bath:id/tv_backprepage"

    // ── 验证页 ──

    /** 验证页的容器（`ll_verify_idserial`）。判定验证页的依据。 */
    const val VERIFY_ID_SERIAL = "com.datalook.bath:id/ll_verify_idserial"

    /** 验证页的账号输入框（`et_id_code`）。 */
    const val ID_CODE_FIELD = "com.datalook.bath:id/et_id_code"

    /** 验证页的密码输入框（`et_id_pwd`）。 */
    const val ID_PWD_FIELD = "com.datalook.bath:id/et_id_pwd"

    /** 验证页的确认按钮（`tvb_id_ok`）。 */
    const val ID_CONFIRM_BUTTON = "com.datalook.bath:id/tvb_id_ok"

    // ── 提示与结果页 ──

    /** 提示页的文本（`tv_hint`）。判定提示页的依据，也是转达给用户的那句话。 */
    const val HINT_TEXT = "com.datalook.bath:id/tv_hint"

    /** 验证结果页的文本（`tv_vf_result`）。判定结果页的依据，同样是转达给用户的那句话。 */
    const val VF_RESULT_TEXT = "com.datalook.bath:id/tv_vf_result"

    // ── 预约 ──

    /** 预约页的容器（`ll_booking`）。判定预约页的依据。 */
    const val BOOKING_ROOT = "com.datalook.bath:id/ll_booking"

    /** 预约码输入页的容器（`ll_verify_bookingcode`）。判定预约码页的依据。 */
    const val BOOKING_CODE_ROOT = "com.datalook.bath:id/ll_verify_bookingcode"
}
