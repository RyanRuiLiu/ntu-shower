# 设备关机机制（实测记录）

> 全部内容来自**反编译源码**或**真机实跑输出**。凡属解释而非直接观测的，一律用
> `〔解释〕` 标出，其余条目都能按「来源」一列复核。
>
> 这份为什么留在这里而不是并进代码注释：设备的开关机定时**不在本仓库任何一行生产代码里**
> ——`SmdtService`、`/dev/McuCom`、浴室 App 与框架 smdt 服务之间的整条调用链都在设备上，
> 而那台设备的 APK 没有源码。反编译与实测是仅有的两条路，这份就是那两条路的记录；
> 并进注释意味着它只服务一个调用点，而它服务的是"后续有人要碰关机这件事"。

---

## 结论

设备的"23:58 关机"**不是**到点读钟再关，而是在某个时刻把**"距现在几小时几分钟"**
写进 MCU 设备 `/dev/McuCom`；写完之后 MCU 自己倒计时。

〔解释〕"写进去的是相对时长而不是绝对时刻"这一条，依据是 `posix.poweronoff` 的四个
参数就是**时、分**（`DataforHandle.settings()` 里现算出来的差值），而不是某个时刻值。

---

## 1. 参与者与文件

| 角色 | 实体 | 来源 |
|---|---|---|
| 发起 | 浴室 App `com.datalook.bath` | 源码 `WelcomeActivity.java:716` |
| 中转 | 框架 smdt 服务 | 实测 `service list` → `11 smdt: [android.app.smdt.ISmdtManager]` |
| 执行 | `com.cpb.smdt_service`／`/system/app/SmdtService.apk` | 实测 `pm path`；文件 28409 字节 |
| 末端 | `libposix.so` → `/dev/McuCom` | 源码 `posix.java`、`DataforHandle.java:108-110` |

`SmdtService.apk` 的 md5：`eb92c5c5107bceeb2d93fff649c991e1`（设备侧 md5sum 与本地解码后
md5 逐位一致）。它在本机的位置：`ntu-bathapp-reverse/samples/SmdtService.apk`，
完整解包产物在 `ntu-bathapp-reverse/exports/output/jadx-smdt-full/`。

---

## 2. 调用链

```text
浴室 App  WelcomeActivity:716
          smdt.smdtSetTimingSwitchMachine(BathParams.autoCloseTime,
                                          BathParams.autoOpenTime, "1")
          → 实参 ("23:58", "10:55", "1")
   ↓
          SmdtManager:225-231   mService.smdtSetPowerOffOnAlarm(offTime, OnTime, enable)
          mService = ServiceManager.getService("smdt")，AIDL
   ↓      ISmdtManager:50       void smdtSetPowerOffOnAlarm(String, String, String)
          ISmdtManager:75       TRANSACTION_smdtSetPowerOffOnAlarm = 6
   ↓      〔实测〕框架那一侧收到后发广播 com.smdt.spot.autoboot
   ↓
          MyReceiver.onReceive  （manifest 注册的 action 之一）
          → BootDao.updatestate(getStringExtra("smdt_settime"))
          → startService(Intent(act=com.cpb.smdt_service.Smdt_service))
   ↓
          Smdt_service.onStart
          → 读 extras: "start" / "stop" / "smdt"
          → BootDao.UpdateStatus()   存 SQLite
          → setupTimer(start, stop, smdt) → DataforHandle.setonoff({start,stop,smdt})
   ↓
          DataforHandle.setonoff → judge → settings(now_h, now_m, on_h, on_m, off_h, off_m)
   ↓
          setPowerOnOff(...) → posix.open("/dev/McuCom") / posix.poweronoff(...) / posix.close
```

参数落位实测（读 `SmdtService` 自己的 SQLite）：

```text
smdt_service 表 →  1 | 10:55 | 23:58 | 1
                   ↑smdt_id ↑on_time ↑off_time ↑power_state
state 表        →  1
```

即 `start` = `autoOpenTime` = `10:55`，`stop` = `autoCloseTime` = `23:58`，`smdt` = `"1"`。

---

## 3. `DataforHandle.settings()` 算法（源码全文）

```java
// com.cpb.smdt_service.entity.DataforHandle
public void judge(String on_time, String off_time, String _state) {
    if (num(on_time) && num(off_time) && num(_state)) {
        settings(Integer.parseInt(nowtime()[0]), Integer.parseInt(nowtime()[1]),   // 当前 时、分
                 Integer.parseInt(on_time.split(":")[0]),  Integer.parseInt(on_time.split(":")[1]),
                 Integer.parseInt(off_time.split(":")[0]), Integer.parseInt(off_time.split(":")[1]));
    }
}

public String[] nowtime() {
    String NOW = new SimpleDateFormat("yyyy-MM-dd kk:mm").format(Calendar.getInstance().getTime());
    return NOW.split(" ")[1].split(":");
}

public void settings(int now_hour, int now_min, int on_hour, int on_min, int off_hour, int off_min) {
    int off_h;
    if (now_hour == 24) now_hour = 0;
    if (off_hour == on_hour && off_min == on_min) return;          // 关=开 → 不设
    boolean byte_off_m = off_min - now_min < 0;
    boolean byte_on_m = on_min - off_min < 0;
    if (off_hour - now_hour < 0) {
        off_h = byte_off_m ? ((off_hour - now_hour) + 24) - 1 : (off_hour - now_hour) + 24;
    } else {
        off_h = byte_off_m ? (off_hour - now_hour) - 1 : off_hour - now_hour;
    }
    int off_m = byte_off_m ? (off_min - now_min) + 60 : off_min - now_min;
    int on_h  = byte_on_m  ? (on_hour - off_hour) - 1 : on_hour - off_hour;
    int on_m  = byte_on_m  ? (on_min - off_min) + 60 : on_min - off_min;
    if (off_h < 0) off_h += 24;
    if (on_h  < 0) on_h  += 24;

    if (Build.MODEL.contains("DS83")) {                            // 本机型号 DS83X → 走这条
        setPowerOnOff((byte) on_h, (byte) on_m, (byte) off_h, (byte) off_m, (byte) 3);
        return;
    }
    if ((on_h == 0 && on_m < 3) || (off_h == 0 && off_m < 3)) {
        // "Time is too short to 3 minutes" —— 本机不经过这里
    } else {
        setPowerOnOff((byte) on_h, (byte) on_m, (byte) off_h, (byte) off_m, (byte) 3);
    }
}

int setPowerOnOff(byte off_h, byte off_m, byte on_h, byte on_m, byte enable) {
    int fd = posix.open("/dev/McuCom", 3, 438);
    posix.poweronoff(off_h, off_m, on_h, on_m, enable, fd);        // 注意实参位置
    posix.close(fd);
    return 0;
}
```

```java
// com.xboot.stdcall.posix
public static final native boolean close(int i);
public static final native int open(String str, int i, int i2);
public static final native int poweronoff(byte b, byte b2, byte b3, byte b4, byte b5, int i);
static { System.loadLibrary("posix"); }
```

要点（都是从上文直接读出的，不是解释）：

- `settings()` 用 `Calendar.getInstance()` 取**当前设备钟**的时、分，与配置的
  `autoCloseTime` / `autoOpenTime` 相减，**化成两组"时:分"的差值**。
- `setPowerOnOff` 的形参名与实参位置**错位**：`settings` 调用时传的是
  `(on_h, on_m, off_h, off_m, 3)`，落到形参 `(off_h, off_m, on_h, on_m, enable)`。
  所以最终 `poweronoff` 收到的前两字节是**开机延时**，后两字节是**关机延时**。
- `enable` 由 `settings` 固定传 `3`；`setonoff` 在 `state == 0` 时传
  `(0, 4, 0, 4, 0)`（即 enable = 0，关闭定时）。
- `state != 0` 才会走 `judge`；`state == 0` 只写一组零值 + enable 0。
- 本机型号 `DS83X` 命中 `Build.MODEL.contains("DS83")`，**跳过**"不足 3 分钟"那条限制。

---

## 4. 实测日志（四组，逐位对账）

### 4.1 `am startservice`（09:23，设备钟 = 真实时间）

命令：

```text
am startservice -n com.cpb.smdt_service/.Smdt_service -e start 10:55 -e stop 23:58 -e smdt 1
```

输出：

```text
Starting service: Intent { cmp=com.cpb.smdt_service/.Smdt_service (has extras) }
I/Smdt_service( 1182): SMDT_service Switch machine onStart
I/Smdt_service( 1182): NOW:,START10:55,STOP23:58
I/Smdt_service( 1182): setup timer
I/DataforHandle( 1182): DataforHandle --- start
I/DataforHandle( 1182): 10 / 55 / 23 / 58 / 1
I/DataforHandle( 1182): ---------------------------2026-10-04 09:23
I/DataforHandle( 1182): For the set of parameters==10===57===14===35
```

手算核对：`now = 09:23`，`on = 10:55`，`off = 23:58`

- `byte_on_m = (55-58) < 0` → true → `on_h = (10-23)-1 = -14` → `+24` → **10**
- `on_m = (55-58)+60` → **57**
- `byte_off_m = (58-23) < 0` → false；`23-9 = 14 ≥ 0` → else → `off_h = 23-9` → **14**
- `off_m = 58-23` → **35**

设备自报 `10===57===14===35`，**逐位一致**。

### 4.2 `service call smdt 6`（09:30，设备钟 = 真实时间）

命令（transaction 6 = `smdtSetPowerOffOnAlarm`，参数顺序同 AIDL 声明）：

```text
service call smdt 6 s16 "23:58" s16 "10:55" s16 "1"
```

输出：

```text
Result: Parcel(00000000    '....')
I/MyReceiver( 1182): Received com.smdt.spot.autoboot, Start switch machine
I/MyReceiver( 1182): Start switch machine,start:10:55,stop:23:58
W/ContextImpl( 1182): Implicit intents with startService are not safe:
    Intent { act=com.cpb.smdt_service.Smdt_service (has extras) } ... MyReceiver.onReceive:57
I/Smdt_service( 1182): SMDT_service Switch machine onStart
I/Smdt_service( 1182): NOW:,START10:55,STOP23:58
I/Smdt_service( 1182): setup timer
I/DataforHandle( 1182): For the set of parameters==10===57===14===28
```

`now = 09:30` → `off_m = 58-30 = 28`，与自报一致。

**这一组是链路的关键证据**：调 AIDL 的第 6 号 transaction，`MyReceiver` **确实**收到了
`com.smdt.spot.autoboot` 广播——框架侧是把 AIDL 调用转成这个广播再往下发的。
同时它也证明了 `start` 收 `10:55`、`stop` 收 `23:58`、`smdt` 收 `"1"`。

### 4.3 跨日拨钟实验（09:24）

```text
设备钟  →  2026-10-05 00:01:00        （跨日）
重下发  →  For the set of parameters==10===57===23===57
拨回    →  2026-10-04 09:24:45
重下发  →  For the set of parameters==10===57===14===34
最终    →  Sun Oct  4 09:24:49 CST 2026 ；SQLite = 10:55|23:58|1
```

跨日那次：`now = 00:01` → `off_h = 23`、`off_m = 58-1 = 57`。
〔解释〕`23:57` 这个延时指向的是**设备钟的下一个 23:58**；由于设备钟已被拨到"次日
00:01"，那个时刻在真实世界里落到了次日，当晚的真实 23:58 被跳过。这一步是换算，不是
直接观测到的"没关机"。

### 4.4 一次真实的关机事件（10-04）

```text
09:35:47Z(=本地 09:35:47)  clock.set "2026-10-04 23:53:40"     ← 最后一次改钟，未跨日
09:41:01Z(=本地 09:41:01)  offline                               ← 5 分 14 秒后设备断线
```

`now = 23:53`、`off = 23:58` → `off_h = 0`、`off_m = 58-53 = 5`，即"5 分钟后关机"。
**关机延时 5 分钟 vs 实测断线间隔 5 分 14 秒**——同一量级，差 14 秒。

同一时刻 `device.clock_skew_s = -51472.18`（约定为「服务端 − 设备」），即设备钟比
服务端快 51472 秒 = 14h17m52s；离线那一刻设备钟读到的是 **23:58:53**。

---

## 5. 触发入口（穷尽 `SmdtService.apk` 全部 9 个 Java 文件后的清单）

`manifest` 原文的相关部分：

```xml
<receiver android:name="com.cpb.smdt_service.MyReceiver">
    <intent-filter>
        <action android:name="android.intent.action.BOOT_COMPLETED"/>
        <action android:name="com.smdt.spot.autoboot"/>
        <action android:name="com.smdt.autoboot.settime"/>
        <action android:name="com.smdt.autoboot.use"/>
    </intent-filter>
</receiver>
<service android:name="com.cpb.smdt_service.Smdt_service">
    <intent-filter android:persistent="true" android:priority="1000">
        <action android:name="com.cpb.smdt_service.Smdt_service"/>
    </intent-filter>
</service>
```

两个组件都没声明 `android:exported`，但都带 `<intent-filter>`；`targetSdkVersion = 17`。
〔解释〕按 Android 的规则这等价于 `exported = true`。实测侧的旁证：`am startservice`
与 `service call` 都成功了。

| 入口 | 动作 | 来源 |
|---|---|---|
| `Smdt_service.onStart`（AIDL / `am startservice`） | 重算并写 MCU | 源码 + 实测 4.1 |
| `MyReceiver` ← `BOOT_COMPLETED`，且 `findstate()=="1"` | 重算并写 MCU | 源码 |
| `MyReceiver` ← `com.smdt.spot.autoboot` | `updatestate` + `startService` → 写 MCU | 源码 + 实测 4.2 |
| `MyReceiver` ← `com.smdt.autoboot.settime` | **只广播回** `on_time`/`off_time`/`power_state` | 源码 |
| `MyReceiver` ← `com.smdt.autoboot.use` | **只** `updatestate`，不写 MCU | 源码 |

**没有 `AlarmManager`、没有 `Handler` 轮询、没有 `Timer`**：`Smdt_service.onCreate`/`onDestroy`
只打日志，`MainActivity.onCreate` 只做 `isRunningServices` 检查。
〔解释〕因此"仅仅是改了设备时间、没有任何组件被触发，MCU 里的延时就会自己跟着变"这件事
在代码里没有对应物。

`DBHelper` 建表（`SMHelper(context, "user", null, 1)`）：

```sql
create table smdt_service(smdt_id int, smdt_on_time varchar(50),
                          smdt_off_time varchar(50), smdt_power_state varchar(50));
create table state(svalue varchar(50));
insert into state values('0');
insert into smdt_service values(1,'9:00','11:00','0');
```

`state` 表**只有一列** `svalue`，所以 `findstate()` 里 `cu.getString(0)` 取到的就是它
（出厂默认 `'0'`，生产上实测为 `'1'`）。`onUpgrade` 为空。

---

## 6. 设备与文件（实测命令输出）

```text
service list                     →  11  smdt: [android.app.smdt.ISmdtManager]
ps                               →  system 504 system_server
                                    u0_a42 1182 com.cpb.smdt_service
ps | grep smdt_mydownload        →  /data/app/com.cpb.smdt_mydownload-1.apk
                                    versionName=1.0.1remote_assistance  DEBUGGABLE
ls -l /system/app/SmdtService.apk        →  28409  2019-04-18
ls -l /system/lib/libposix.so            →  17844  2019-04-18
ls -l /dev/McuCom                        →  crwxrwxrwx root root 251, 0
ls -l /data/data/com.cpb.smdt_service/databases/user  →  20480（另有 -journal）
dumpsys alarm | grep datalook|smdt|bath  →  （无输出；该 dump 共 87 行）
dumpsys alarm 头部                       →  Next shutdown wakeup: 0
```

`/dev/McuCom` 权限是 `crwxrwxrwx`——**任何进程都能打开它**。

---

## 7. 浴室 App 侧的参数（真机日志，7 月到 10 月未变）

来自 `/sdcard/datalook/log/Params_*.txt` 与其中的 `V8下载参数` 行：

```text
读取参数[autoOpenTime:10:55][autoCloseTime:23:58][closepassword:<已脱敏>]

V8下载参数[...,maxbathtime:1200,maxinspacetime:300,maxbathspacetime:300,
           supportoffline:1,offlinenum:5,posserverIp:<已脱敏>,
           posserverPort:5500,price:<已脱敏>,poscode:<已脱敏>]

CommandList: [{"CommandLength":"4","CommandKey":"A0","CommandValue":"00002359"}]
```

`CommandList` 这一项**在浴室 App 的源码里一次都没出现**（全包 grep 无命中），也**在
`SmdtService` 的全部 9 个文件里没有出现**。

---

## 8. 设备上可用的工具（实测）

```text
有：busybox-smp（含 base64）、sqlite3、grep、dumpsys、logcat、am、service、ps、dd
无：base64、wc、head、unzip
```

`device.exec` 的返回体上限实测约 32 KiB：一次 `busybox-smp base64` 输出 32661 字符时被
截断（完整应为 37880 字符），改为两块 `head -c 15000` / `tail -c +15001` 拼接后 md5 一致。

---

## 9. 本机拉取产物

| 路径 | 说明 |
|---|---|
| `ntu-bathapp-reverse/samples/SmdtService.apk` | 28409 字节，md5 `eb92c5c5107bceeb2d93fff649c991e1` |
| `ntu-bathapp-reverse/exports/output/jadx-smdt-full/` | 完整解包（9 个 java + manifest + res） |

---

## 10. 未验证（2026-10-04 晚已全部落定，见 §11）

- ~~**§4.4 那次关机的前因**~~ → 已定论：**没有任何一次 `settings()` 被触发**。拨钟
  本身就是触发器，见 §11。
- ~~**`CommandList` 的实际消费者**~~ → 排除了 `smdt_mydownload`（全包无
  McuCom/CommandList/poweronoff 痕迹）与框架 `SmdtService`（见 §11）。剩下唯一候选
  是"V8 平台下发了但本部署无人消费"：观测到的关机跟随 SmdtService 的 23:58，而不是
  CommandList 的 23:59。
- ~~**框架侧实现**~~ → 已拉取 `services.jar` 并反编译：`com.android.server.SmdtService`
  只做两件事——把 AIDL 调用转成 `com.smdt.spot.autoboot` 广播（extras: `smdt_settime`
  / `smdt` / `start` / `stop`），以及 `shutdown()`（发
  `ACTION_REQUEST_SHUTDOWN`）。**没有时间变化监听、没有直写 `/dev/McuCom`、没有
  CommandList 消费**。产物在 `ntu-bathapp-reverse` 工作区外本机
  `/tmp/ntu-probe/incident/services_src/`（未入库）。

---

## 11. 2026-10-04 事故定论：拨钟为什么直接杀死设备

上午 09:41（北京）设备断电，14:20 才人工恢复（`booted_at = 06:19:13Z`，APP 14:20:03
重启并重新拉参）。链路事件：`01:41:01.201Z offline → 06:20:02.913Z online`，死了
4h39m。§4.4 已记录表面时序，本节是机制定论。

**机制（2026-10-05 凌晨受控实验最终定论，修正"对跳钟免疫"的中间结论；
工具：`/data/local/tmp/mcu.jar`，dalvikvm 直调 libposix 的 JNI，绕开 SmdtService
与浴室 App）**：

`posix.poweronoff(on_h, on_m, off_h, off_m, enable, fd)` = `ioctl(/dev/McuCom, 0xA1,
5 字节)`。5 字节是**相对延时**：`[开机延时 h, m, 关机延时 h, m, enable]`——开机延时
**从断电时刻起算**。MCU 收到后按真实时间倒计时：到关机延时断电，再过开机延时上电。
接口全表（本驱动实测）：`0xA1` poweronoff、`0xA4` getfwver（实测 0xB1）、`0xA6`
mcugetcmdvalue（本驱动未实现，全部 -1）、`0xA8` mcusetcmd；`enable` 的 `3`=关机+开机
都使能，`0`=全不使能。

关键行为（各至少两次独立验证）：

- **截止时刻随钟走**：钟被拨到越过已布防的截止时刻 → **立即断电**（23:27 实验：
  布防 3 分钟、拨钟 +3.5 分越过截止 → 拨钟瞬间断电）；钟回拨 → 断电**等量推迟**
  （01:59 事故：`clock.set` -2h + `bath.restart` 布防 67 分 → 断电落在真实
  01:58:40，与预测一致）。"钟到 23:58 就关"的实质是"钟到达布防时算出的那个时刻
  就关"。
- **相对延时本身不依赖钟**：布防时长按真实时间流逝，钟在布防后的跳变只会平移
  "钟面截止"，不存在"跳变让倒计时凭空消失"或"凭空提前"以外的第三种效应。
- **布防路径可靠性差异**：SmdtService 冷启动会**静默丢布防**（AMS "Waited long
  enough"，SQLite 不更新；运行时 APP 重启触发的重布走这条冷路，实测丢过）；
  开机 `BOOT_COMPLETED` 的重布可靠（9 月整月节奏实证）。**dalvikvm 直调路径每次
  open/ioctl/close、无进程状态，最可靠**。
- MCU 无可用读回（0xA6 未实现），验证只能靠行为：断/上电时刻，服务端链路事件
  滞后 0–90 秒。

**日常节律的实证**（生产库按天首末帧，2026-09-20 起的常规日）：

```text
每天首帧  02:56–03:00Z  = 北京 10:56–11:00   ← autoOpenTime 10:55，MCU 自动上电
每天末帧  15:03–15:58Z  = 北京 23:03–23:58   ← autoCloseTime 23:58，MCU 断主电
```

**早上事故的序列（最终版）**：09:23/09:30 两次实验布防把"关机延时"写到
约 14.5 小时后（钟面截止 23:58 附近）→ 09:35:47 拨钟到 23:53:40，钟面截止被拖到
**眼前**（剩 5 分钟）→ 09:41 断电。拨钟本身不改倒计时的"存在"，改的是它相对钟面
的剩余量。

**power.schedule 的前身（2026-10-04 的手动验证记录）**：当时延长服务靠 APP 重启
重读计划重写 MCU，这里的手动布防证明了"直接重布即可，**不拨钟、不重启 APP、不碰
会话时间轴**"。这条 dalvikvm 路径现已封装为 `power.schedule` 动作（agent 内经
libposix.so 直调 ioctl）：

```bash
# device.exec（维护通道）在设备上执行；延时按"距现在"换算
LD_LIBRARY_PATH=/system/lib dalvikvm -cp /data/local/tmp/mcu.jar mcu.Main on <onH> <onM> <offH> <offM> 3
# 例：23:50 延长 70 分钟 → mcu.Main on 10 55 1 10 3   （关机延时 1h10m → 01:00 断电）
# 写回：按"距现在的 23:58"换算重新布防；23:58 已过则布到次日同一时刻
```

现行机制不在本文复述：延长是**一次布防**（断电目标 = 次日 auto_close_time，整夜覆盖；
恢复时墙钟写回真实轴；重布替换规则与 enable 位的实验依据见 §12），状态机与全部判据见
`server/app/modules/power/service.py` 的模块注释与
[ADR-0025](adr/0025-single-arming-hold.md)；动作行为见
`server/app/core/actions.py` 的 `POWER_SCHEDULE`；钟偏差的自动校正（`time_sync_enabled`）
也在同一处。

### 运维结论

- **危险动作 = 钟不对时布防**：布防按当时钟面换算"距 23:58 还有多久"，钟落在
  `[当前, 23:58)` 窗口内时算出的是贴近当前时刻的极短延时（今早 09:41 事故）。拨钟后要先拨回
  真实时间再布防。
- **拨过已布防的截止时刻 = 立即断电**；回拨 = 等量推迟。两者都是钟面判定。
- 紧急停用定时：`service call smdt 6 s16 "23:58" s16 "10:55" s16 "0"`（enable=0）；
  恢复第三参给 `"1"`。注意 AIDL 与 `am startservice` 都覆盖 SmdtService 的 SQLite，
  且冷启动存在静默丢失的情况——发完查 SQLite 与 logcat 确认。
- `configure.xml` 里的 `AutoOpenTime=08:20/AutoCloseTime=17:35` **不生效**：运行时
  以 V8 下载参数（10:55/23:58）为准，`SmdtService` 库里落的也是后者。
- V8 平台真实地址：configure.xml `serverIP = http://210.29.68.22` + `serverPort=80`，
  基路径 `/openplartform/business/`——验证查询、扣款、阀事件、流水上报、心跳全走它。

---

## 12. 2026-11-21 受控实验：重布替换规则、0 延时与 enable 位（四组定论）

§11 定论了钟面判定与拨钟效应；本节钉死**多次写入之间的关系**与 **0 延时 / enable=0**
两个遗留疑点——它们是单次布防状态机（[ADR-0025](adr/0025-single-arming-hold.md)）
与取消路径成立的前提：布防"只有一次"依赖"最后写入唯一生效"，取消依赖"重布更早
截止时生效"。工具与路径同 §11（`mcu.jar` dalvikvm 直调 libposix）；实验机 2F
（1c94310a536777d6）；每组布防的开机延时恒 1 分钟（断电后 MCU 自动回电）。

时刻口径：布防取 `command_audits.created_at`（UTC，动作下发的时刻，ioctl 在其后
秒级落地）；断/上电取 `device_link_events`（UTC，滞后 0–90 秒）；本地 = UTC+8。
两条链路特征先立此存照：**断电→回连恒 ~3m15–27s**（MCU 上电延时 1 分钟 + 安卓开机
~2.5 分钟），网络闪断只有 13–14 秒——判读链路事件时以离线时长区分，本节全部断电
结论都按此复核过。

### 12.1 探针 A：重布更长截止——生效

17:17:22 布防 off=+6m enable=3；17:17:50 重布 off=+30m enable=3。
首次截止 17:23:22 无断电（链路事件全程无离线）→ **重布替换了首次截止，更长生效**。

### 12.2 探针 B：重布更短截止——也生效

17:24:52 布防 off=+12m；17:25:19 重布 off=+5m。
17:31:03 断电（重布截止 = 17:30:19，链路滞后 44 秒）→ **更短截止也生效**：重布
没有「只许延长」的限制。布防 +12m 的截止 17:36:52 被重布清除，未触发。

### 12.3 探针 C：0 延时——没有「立即断电」语义

17:34:46 布防 off=+8m；17:35:06 重布 off=+0m（on=+1m）。
设备 17:38:38 才断电——重布后 **~3m32s**，不是立即。0 延时在 MCU 内部有分钟粒度
以外的处理（机制无法从外部观测，行为已钉死：写入后约 3–4 分钟才断）。
顺带的旁证：A 组的 +30m 截止（17:47:50）未及触发就被本组重布覆盖——再次印证
替换规则；断电后 MCU 布防周期已走完（off 已发、on 已发），17:42:05 回连。

### 12.4 探针 D：enable=0——使挂起布防整体失效，但不可用于取消

两轮独立验证：

- 第一轮：17:46:28 布防 off=+6m enable=3，17:48:40 重布 off=+30m enable=0 → 原截止
  17:52:28 已过而设备未断电。写入未被忽略（否则原截止照常断电），挂起布防失效。
- 第二轮（短窗设计，重布改配 +2m，把等待从 30 分钟压到 9 分钟）：18:01:13 布防
  off=+8m enable=3，18:03:22 重布 off=+2m enable=0 → 重布截止 18:05:22 与原截止
  18:09:13 两个观测窗全过、无断电——enable=0 既不布上新截止，也使原挂起截止不再
  触发。（第一轮的 +30m 截止未待复核即被第二轮布防覆盖——再次印证替换规则。）

定论：**enable=0 使挂起布防整体失效**（清除或掩蔽，外部不可分），且不布新截止。

**但它不能当取消原语用**：hold 布防是一对目标（off=次日 auto_close_time，**on=常规清晨**），
enable=0 连开机定时一起失效——今晚既没有重布的近截止，清晨也无人上电，常规夜的
断电/上电循环两头失效。取消路径保持「重布更早截止」（恢复时刻前的手动恢复：先写准
墙钟，再布 off=now+90s，on 按常规清晨换算）：B 组已证其生效，失败方向安全（取消
失败时断电仍由整夜覆盖布防的截止承担）。

### 12.5 定论与生产含义

1. **最后写入唯一生效，无方向限制**（A/B/D 三向验证）：同一 MCU 上后一次 0xA1
   写入整体替换前一次布防——更长、更短、enable=0 都生效。单次布防状态机的语义
   基础（ADR-0025）：整晚只布一次，取消靠重布更早截止。
2. **任何路径不得写 0 延时**（C）：无立即语义，约 3–4 分钟后才断。取消路径的
   「更早截止」取 now+90s，避免与 0 延时重合；整夜覆盖布防的截止恒在次日，天然远离 0。
3. **enable=0 使挂起布防整体失效且不布新截止，禁用于取消**（D 两轮验证）：它连
   清晨上电一起失效，常规夜循环两头失效（见 12.4）。§11 的「紧急停用定时」运维
   结论（AIDL enable=0）同此风险，仅限现场人工、知情使用。
4. **0 延时与 enable=0 是仅有的两个反直觉输入**：其余语义（相对延时换算、钟面
   判定、拨钟效应）见 §11，全部有至少两次独立验证。
