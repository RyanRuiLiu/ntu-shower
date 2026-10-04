# 设备关机机制（实测记录）

> 全部内容来自**反编译源码**或**真机实跑输出**。凡属解释而非直接观测的，一律用
> `〔解释〕` 标出，其余条目都能按「来源」一列复核。
>
> 这份为什么留在这里而不是并进代码注释：设备的开关机定时**不在本仓库任何一行生产代码里**
> ——`SmdtService`、`/dev/McuCom`、浴室 App 与框架 smdt 服务之间的整条调用链都在设备上，
> 而那台设备的 APK 没有源码。反编译与实测是仅有的两条路，这份就是那两条路的记录；
> 并进注释意味着它只服务一个调用点，而它服务的是"以后再有人要碰关机这件事"。

---

## 一句话

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
md5 逐位一致）。它在本机的位置：`ntu-bathapp-reverse/apks/SmdtService.apk`，
完整解包产物在 `ntu-bathapp-reverse/output/jadx-smdt-full/`。

---

## 2. 调用链

```
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

```
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

```
am startservice -n com.cpb.smdt_service/.Smdt_service -e start 10:55 -e stop 23:58 -e smdt 1
```

输出：

```
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

```
service call smdt 6 s16 "23:58" s16 "10:55" s16 "1"
```

输出：

```
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

```
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

```
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

```
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

```
读取参数[autoOpenTime:10:55][autoCloseTime:23:58][closepassword:123456]

V8下载参数[...,maxbathtime:1200,maxinspacetime:300,maxbathspacetime:300,
           supportoffline:1,offlinenum:5,posserverIp:192.168.16.69,
           posserverPort:5500,price:0.3888888888888889,poscode:5026]

CommandList: [{"CommandLength":"4","CommandKey":"A0","CommandValue":"00002359"}]
```

`CommandList` 这一项**在浴室 App 的源码里一次都没出现**（全包 grep 无命中），也**在
`SmdtService` 的全部 9 个文件里没有出现**。

---

## 8. 设备上可用的工具（实测）

```
有：busybox-smp（含 base64）、sqlite3、grep、dumpsys、logcat、am、service、ps、dd
无：base64、wc、head、unzip
```

`device.exec` 的返回体上限实测约 32 KiB：一次 `busybox-smp base64` 输出 32661 字符时被
截断（完整应为 37880 字符），改为两块 `head -c 15000` / `tail -c +15001` 拼接后 md5 一致。

---

## 9. 本机拉取产物

| 路径 | 说明 |
|---|---|
| `ntu-bathapp-reverse/apks/SmdtService.apk` | 28409 字节，md5 `eb92c5c5107bceeb2d93fff649c991e1` |
| `ntu-bathapp-reverse/output/jadx-smdt-full/` | 完整解包（9 个 java + manifest + res） |

---

## 10. 尚未核实的点

- **§4.4 那次关机的前因**：09:35:47 改钟之后、09:41:01 断线之前，是哪一次调用触发了
  `settings()`。设备侧可查的两处证据（`/sdcard/datalook/log/Params_*.txt` 里 App 启动
  写的那一行、`databases/user` 的 mtime）都要等设备恢复后才能读，届时才能定论。
- **`CommandList` 的实际消费者**：它不在浴室 App 源码里，也不在 `SmdtService` 里，
  尚未定位到谁读它。
- **框架侧实现**：`ISmdtManager` 的 Stub 本体在 framework（`services.jar` 1.4 MB /
  `framework.jar` 3.7 MB，均在 `/system/framework/`），未拉取核对。已知的观测事实是
  §4.2 那条：调 AIDL 会引出 `com.smdt.spot.autoboot` 广播。
