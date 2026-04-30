#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import json
import shutil
import subprocess
import urllib.request
import base64
import datetime
import platform
from pathlib import Path

# ──────────────────────────────────────────────
#  架构映射（自动检测）
# ──────────────────────────────────────────────
_ARCH_MAP = {
    "x86_64" : "x86_64-unknown-linux-gnu",
    "aarch64": "aarch64-unknown-linux-gnu",
    "armv7l" : "armv7-unknown-linux-gnueabihf",
}

def _detect_arch() -> str:
    machine = platform.machine()
    arch = _ARCH_MAP.get(machine)
    if not arch:
        die(f"不支持的 CPU 架构：{machine}，支持列表：{list(_ARCH_MAP.keys())}")
    ok(f"CPU 架构：{machine} → {arch}")
    return arch

# 运行时确定，不在模块级写死
SS_ARCH: str = ""   # 由 main() 中 _detect_arch() 赋值


# ──────────────────────────────────────────────
#  全局常量
# ──────────────────────────────────────────────
WORK_DIR        = Path("/tmp")
BACKUP_DIR      = Path("/etc/twist")
SS_CONFIG_DIR   = Path("/etc/shadowsocks-rust")
SS_CONFIG_FILE  = SS_CONFIG_DIR / "config.json"
SS_SERVICE_FILE = Path("/etc/systemd/system/shadowsocks-rust.service")
OBFS_DIR        = Path("/usr/local/simple-obfs")
OBFS_BIN        = OBFS_DIR / "bin" / "obfs-server"

METHOD          = "aes-256-gcm"
PORT            = 443
OBFS            = "tls"
OBFS_HOST       = "microsoft.com"
OBFS_URI        = "/"

# ══════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════

def run(cmd, check=True, capture=False, shell=False, input=None):
    if isinstance(cmd, str) and not shell:
        cmd = cmd.split()
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        shell=shell,
        input=input,
    )

def run_shell(cmd, check=True, capture=False):
    return run(cmd, check=check, capture=capture, shell=True)

def info(msg):  print(f"\033[1;34m[INFO]\033[0m  {msg}")
def ok(msg):    print(f"\033[1;32m[ OK ]\033[0m  {msg}")
def warn(msg):  print(f"\033[1;33m[WARN]\033[0m  {msg}")

def die(msg):
    print(f"\033[1;31m[ERR ]\033[0m  {msg}", file=sys.stderr)
    sys.exit(1)

def require_root():
    if os.geteuid() != 0:
        die("请使用 root 权限运行此脚本（sudo python3 twist.py）")

def timestamp():
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S")

def backup(src: Path):
    """备份文件到 /etc/twist/filename.old-时间戳，失败则退出"""
    if not src.exists():
        warn(f"备份目标不存在，跳过：{src}")
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / f"{src.name}.old-{timestamp()}"
    try:
        shutil.copy2(src, dst)
        ok(f"已备份：{src} → {dst}")
    except Exception as e:
        die(f"备份失败：{src} → {dst}：{e}")

def prompt(msg, default=""):
    suffix = f" [{default}]" if default else ""
    val = input(f"\033[1;33m{msg}{suffix}: \033[0m").strip()
    return val if val else default

# ══════════════════════════════════════════════
#  步骤 1 — 前置准备
# ══════════════════════════════════════════════

def step_1_prerequisites():
    """
    步骤 1：前置准备
    - 切换工作目录到 /tmp
    - 创建备份目录 /etc/twist
    - apt update & upgrade
    - 安装所有依赖包
    - 检测出口网卡（有多个则让用户选择，没有则退出）
    - 获取公网 IPv4 / IPv6
    - 获取 MTU
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 1：前置准备")
    info("══════════════════════════════════════════════════════")

    require_root()
    _prepare_work_and_backup_dir()
    _apt_update_upgrade()
    _apt_install_deps()

    eth  = _detect_eth()
    ipv4 = _get_public_ipv4()
    ipv6 = _get_public_ipv6(eth)
    mtu  = _get_mtu(eth)

    ok("步骤 1 完成\n")
    return eth, ipv4, ipv6, mtu

def _prepare_work_and_backup_dir():
    try:
        os.chdir(WORK_DIR)
        ok(f"工作目录：{WORK_DIR}")
    except Exception as e:
        die(f"无法切换到工作目录 {WORK_DIR}：{e}")

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ok(f"备份目录已就绪：{BACKUP_DIR}")
    except Exception as e:
        die(f"无法创建备份目录 {BACKUP_DIR}：{e}")

def _apt_update_upgrade():
    info("执行 apt update ...")
    r = run("apt-get update", check=False)
    if r.returncode != 0:
        die("apt update 失败，请检查网络或软件源配置")
    ok("apt update 完成")

    info("执行 apt upgrade ...")
    r = run(["apt-get", "upgrade", "-y"], check=False)
    if r.returncode != 0:
        die("apt upgrade 失败")
    ok("apt upgrade 完成")

def _apt_install_deps():
    packages = [
        "wget", "gawk", "grep", "curl", "sed", "git",
        "gcc", "swig", "gettext", "autoconf", "automake",
        "make", "libtool", "perl", "cpio", "xmlto", "asciidoc",
        "cron", "net-tools", "dnsutils", "rng-tools",
        "libc-ares-dev", "libev-dev", "openssl", "libssl-dev",
        "zlib1g-dev", "libpcre3-dev", "libevent-dev",
        "build-essential", "python3-dev", "python3-pip",
        "python3-setuptools", "python3-qrcode",
        "nginx", "fail2ban", "ufw",
    ]
    info(f"安装依赖包（共 {len(packages)} 个）...")
    r = run(["apt-get", "install", "-y"] + packages, check=False)
    if r.returncode != 0:
        die("依赖包安装失败，请检查软件源或网络")
    ok("依赖包安装完成")

def _detect_eth():
    """
    检测出口网卡：
    - 找到唯一一个则直接使用
    - 找到多个则让用户选择
    - 找不到则退出
    """
    candidates = []

    # 方式一：route
    r1 = run_shell(
        "route | grep '^default' | grep -o '[^ ]*$'",
        check=False, capture=True,
    )
    for line in r1.stdout.strip().splitlines():
        name = line.strip()
        if name and name not in candidates:
            candidates.append(name)

    # 方式二：ip route（补充去重）
    r2 = run_shell(
        "ip -4 route list 0/0 | grep -Po '(?<=dev )(\\S+)'",
        check=False, capture=True,
    )
    for line in r2.stdout.strip().splitlines():
        name = line.strip()
        if name and name not in candidates:
            candidates.append(name)

    if not candidates:
        die("无法检测到出口网卡，请检查网络配置后重试")

    if len(candidates) == 1:
        ok(f"检测到出口网卡：{candidates[0]}")
        return candidates[0]

    # 多个网卡，让用户选择
    print()
    info("检测到多个候选出口网卡，请选择：")
    for idx, name in enumerate(candidates, 1):
        print(f"  [{idx}] {name}")
    while True:
        choice = input(
            f"\033[1;33m请输入编号 [1-{len(candidates)}]: \033[0m"
        ).strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            selected = candidates[int(choice) - 1]
            ok(f"已选择网卡：{selected}")
            return selected
        warn(f"无效输入，请输入 1 到 {len(candidates)} 之间的数字")

def _get_public_ipv4():
    info("获取公网 IPv4 地址 ...")

    # 优先用 dig
    r = run_shell(
        "dig @resolver1.opendns.com -t A -4 myip.opendns.com +short",
        check=False, capture=True,
    )
    ipv4 = r.stdout.strip()
    if ipv4:
        ok(f"公网 IPv4：{ipv4}")
        return ipv4

    # 备用 HTTP
    for url in ["https://api4.ipify.org", "https://ipv4.icanhazip.com"]:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ipv4 = resp.read().decode().strip()
                ok(f"公网 IPv4（{url}）：{ipv4}")
                return ipv4
        except Exception:
            continue

    die("无法获取公网 IPv4 地址，请检查网络后重试")


def _get_public_ipv6(eth):
    r = run_shell(f"ip -6 addr show {eth}", check=False, capture=True)
    if not r.stdout.strip():
        info("未检测到 IPv6 地址，跳过")
        return ""

    info("检测到 IPv6，正在获取公网 IPv6 ...")
    r2 = run_shell(
        "curl -s diagnostic.opendns.com/myip",
        check=False, capture=True,
    )
    ipv6 = r2.stdout.strip()
    if ipv6:
        ok(f"公网 IPv6：{ipv6}")
    else:
        warn("无法获取公网 IPv6，IPv6 功能将被禁用")
        return ""
    return ipv6

def _get_mtu(eth):
    mtu_file = Path(f"/sys/class/net/{eth}/mtu")
    if mtu_file.exists():
        try:
            mtu = int(mtu_file.read_text().strip())
            ok(f"MTU：{mtu}")
            return mtu
        except ValueError:
            pass
    warn(f"无法读取 {eth} 的 MTU，默认使用 1492")
    return 1492

# ══════════════════════════════════════════════
#  步骤 2 — 开启 BBR 算法
# ══════════════════════════════════════════════

def step_2_enable_bbr():
    """
    步骤 2：开启 BBR 算法
    - 检查是否在 OpenVZ 环境（是则退出）
    - 检查内核版本（< 4.9 则警告，使用 cubic 继续）
    - 备份 sysctl.conf
    - 检查 BBR 支持，加载模块，写入配置
    - 若不支持则警告使用 cubic 并继续
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 2：开启 BBR 算法")
    info("══════════════════════════════════════════════════════")

    _check_openvz()
    kernel_ok = _check_kernel_version()
    backup(Path("/etc/sysctl.conf"))

    if kernel_ok:
        _enable_bbr()
    else:
        warn("内核版本不足 4.9，无法启用 BBR，将保持默认 cubic 算法，继续安装")

    ok("步骤 2 完成\n")


def _check_openvz():
    if Path("/proc/user_beancounters").exists():
        die("检测到 OpenVZ 虚拟化环境，不支持 BBR 拥塞控制算法，退出安装")
    ok("非 OpenVZ 环境，继续")

def _check_kernel_version():
    """返回 True 表示内核 >= 4.9"""
    r = run_shell(
        "uname -r | grep -oE '[0-9]+\\.[0-9]+'",
        capture=True, check=False,
    )
    ver_str = r.stdout.strip().splitlines()[0] if r.stdout.strip() else "0.0"
    info(f"当前内核版本：{ver_str}")
    try:
        major, minor = map(int, ver_str.split("."))
        if (major, minor) >= (4, 9):
            ok(f"内核版本 {ver_str} >= 4.9，支持 BBR")
            return True
        else:
            warn(f"内核版本 {ver_str} < 4.9，不支持 BBR")
            return False
    except ValueError:
        warn(f"无法解析内核版本：{ver_str}")
        return False

def _enable_bbr():
    r = run_shell(
        "sysctl net.ipv4.tcp_available_congestion_control",
        capture=True, check=False,
    )
    info(f"可用拥塞控制算法：{r.stdout.strip()}")

    if "bbr" not in r.stdout:
        ko = run_shell(
            "ls /lib/modules/$(uname -r)/kernel/net/ipv4/tcp_bbr.ko* 2>/dev/null",
            capture=True, check=False,
        )
        if ko.stdout.strip():
            ok(f"发现 BBR 模块：{ko.stdout.strip()}")
            result = run("modprobe tcp_bbr", check=False)
            if result.returncode != 0:
                warn("tcp_bbr 模块加载失败，将使用默认 cubic 算法，继续安装")
                return
            ok("tcp_bbr 模块加载成功")
        else:
            warn("未找到 tcp_bbr.ko 模块，将使用默认 cubic 算法，继续安装")
            return

    run_shell("sed -i '/net.ipv4.tcp_congestion_control/d' /etc/sysctl.conf")
    run_shell('echo "net.ipv4.tcp_congestion_control = bbr" >> /etc/sysctl.conf')
    ok("BBR 已写入 /etc/sysctl.conf")

    run_shell("sysctl -p", check=False)

    verify = run_shell(
        "sysctl net.ipv4.tcp_congestion_control",
        capture=True, check=False,
    )
    info(f"当前拥塞控制：{verify.stdout.strip()}")

# ══════════════════════════════════════════════
#  步骤 3 — 安装 shadowsocks-rust（自动获取最新版）
# ══════════════════════════════════════════════

def step_3_install_shadowsocks():
    """
    步骤 3：安装 shadowsocks-rust
    - 从 GitHub API 自动获取最新版本号
    - 下载对应架构的 .tar.xz
    - SHA256 校验（有则校验，无则跳过并警告）
    - 解压到 /usr/local/bin
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 3：安装 shadowsocks-rust（自动获取最新版）")
    info("══════════════════════════════════════════════════════")

    version, sha256 = _get_latest_ss_version()
    tarball = _download_shadowsocks(version)
    if sha256:
        _verify_sha256(tarball, sha256)
    else:
        warn("未能获取官方 SHA256，跳过校验（请自行确认文件完整性）")
    _extract_shadowsocks(tarball)

    ok("步骤 3 完成\n")


def _get_latest_ss_version():
    """
    从 GitHub Releases API 获取最新版本号和 SHA256。
    返回 (version_str, sha256_str_or_empty)
    """
    info("正在从 GitHub API 获取最新版本信息 ...")
    api_url = (
        "https://api.github.com/repos/shadowsocks/shadowsocks-rust"
        "/releases/latest"
    )
    try:
        req = urllib.request.Request(
            api_url,
            headers={
                "Accept"    : "application/vnd.github+json",
                "User-Agent": "curl/8.5.0",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        die(f"无法获取最新版本信息：{e}\n请检查网络后重试")

    version = data.get("tag_name", "").lstrip("v")
    if not version:
        die("无法从 GitHub API 响应中解析版本号")
    ok(f"最新版本：v{version}")

    sha256 = _extract_sha256_from_release(data, version)
    return version, sha256

def _extract_sha256_from_release(data: dict, version: str) -> str:
    """从 release body 文本中提取对应架构 .tar.xz 的 SHA256，找不到返回空字符串"""
    target = f"shadowsocks-v{version}.{SS_ARCH}.tar.xz"
    body   = data.get("body", "")
    # 常见格式：<64位hex>  filename  或  <64位hex> *filename
    m = re.search(r"([0-9a-fA-F]{64})\s+\*?" + re.escape(target), body)
    if m:
        sha256 = m.group(1).lower()
        ok(f"已从 Release Notes 提取 SHA256：{sha256}")
        return sha256
    warn("未能从 Release Notes 中提取 SHA256")
    return ""


def _download_shadowsocks(version: str) -> Path:
    tarball_name = f"shadowsocks-v{version}.{SS_ARCH}.tar.xz"
    dest = WORK_DIR / tarball_name
    url  = (
        f"https://github.com/shadowsocks/shadowsocks-rust"
        f"/releases/download/v{version}/{tarball_name}"
    )

    if dest.exists():
        warn(f"{tarball_name} 已存在，跳过下载")
        return dest

    info(f"下载 {url} ...")
    r = run(
        ["curl", "-L", "--fail", "--progress-bar", "-o", str(dest), url],
        check=False,
    )
    if r.returncode != 0 or not dest.exists():
        die(f"下载失败：{url}")
    ok(f"下载完成：{dest}")
    return dest

def _verify_sha256(tarball: Path, expected: str):
    info("校验 SHA256 ...")
    r = run_shell(f"sha256sum {tarball}", capture=True, check=False)
    if r.returncode != 0:
        die("sha256sum 执行失败")
    actual = r.stdout.split()[0].strip().lower()
    info(f"期望值：{expected}")
    info(f"实际值：{actual}")
    if actual != expected.lower():
        die("SHA256 校验失败！文件可能已损坏或被篡改，请重新运行脚本")
    ok("SHA256 校验通过")

def _extract_shadowsocks(tarball: Path):
    info(f"解压 {tarball.name} 到 /usr/local/bin ...")
    r = run(["tar", "Jxf", str(tarball), "-C", "/usr/local/bin"], check=False)
    if r.returncode != 0:
        die("解压失败，请确认 tar 支持 xz 格式（需要 xz-utils）")
    ok("解压完成")

# ══════════════════════════════════════════════
#  步骤 3.1 — 创建 systemd 服务
# ══════════════════════════════════════════════

def step_3_1_create_service():
    """
    步骤 3.1：创建 systemd 服务
    - 写入 /etc/systemd/system/shadowsocks-rust.service
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 3.1：创建 systemd 服务")
    info("══════════════════════════════════════════════════════")

    _write_ss_service()
    ok("步骤 3.1 完成\n")


def _write_ss_service():
    service_content = f"""\
[Unit]
Description=Shadowsocks-Rust Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/ssserver -c {SS_CONFIG_FILE}
Restart=on-failure
LimitNOFILE=512000

[Install]
WantedBy=multi-user.target
"""
    try:
        SS_SERVICE_FILE.write_text(service_content, encoding="utf-8")
        ok(f"服务文件已写入：{SS_SERVICE_FILE}")
    except Exception as e:
        die(f"写入服务文件失败：{e}")


# ══════════════════════════════════════════════
#  步骤 3.2 — 安装 simple-obfs
# ══════════════════════════════════════════════

def step_3_2_install_simple_obfs():
    """
    步骤 3.2：安装 simple-obfs
    - git clone 到 /tmp/simple-obfs
    - git submodule update --init --recursive
    - ./autogen.sh → ./configure → make → make install
    - 安装完成后恢复工作目录到 /tmp
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 3.2：安装 simple-obfs")
    info("══════════════════════════════════════════════════════")

    _clone_simple_obfs()
    _build_simple_obfs()

    if not OBFS_BIN.exists():
        die(f"simple-obfs 安装后未找到可执行文件：{OBFS_BIN}")

    ok(f"步骤 3.2 完成，obfs-server 路径：{OBFS_BIN}\n")

def _clone_simple_obfs():
    obfs_src = WORK_DIR / "simple-obfs"
    if obfs_src.exists():
        warn("simple-obfs 源码目录已存在，跳过 clone")
        return
    info("克隆 simple-obfs 仓库 ...")
    r = run([
        "git", "clone",
        "https://github.com/shadowsocks/simple-obfs.git",
        str(obfs_src),
    ], check=False)
    if r.returncode != 0:
        die("git clone simple-obfs 失败，请检查网络")
    ok("clone 完成")

def _build_simple_obfs():
    obfs_src = WORK_DIR / "simple-obfs"
    try:
        os.chdir(obfs_src)
        ok(f"进入目录：{obfs_src}")
    except Exception as e:
        die(f"无法进入 simple-obfs 目录：{e}")

    steps = [
        (["git", "submodule", "update", "--init", "--recursive"], "拉取子模块"),
        (["./autogen.sh"],                                         "执行 autogen.sh"),
        (["./configure", f"--prefix={OBFS_DIR}"],                  "执行 configure"),
        (["make"],                                                  "执行 make"),
        (["make", "install"],                                       "执行 make install"),
    ]
    for cmd, desc in steps:
        info(f"{desc} ...")
        r = run(cmd, check=False)
        if r.returncode != 0:
            die(f"simple-obfs 构建失败（{desc}），退出安装")
        ok(f"{desc} 完成")

    # 恢复工作目录
    os.chdir(WORK_DIR)
    ok(f"工作目录恢复：{WORK_DIR}")

# ══════════════════════════════════════════════
#  步骤 4 — 配置 shadowsocks-rust
# ══════════════════════════════════════════════

def step_4_configure_shadowsocks(ipv6, mtu):
    """
    步骤 4：配置 shadowsocks-rust
    - 根据是否有 IPv6 决定监听地址和 DNS
    - 用 ssservice genkey 生成密码
    - 写入 /etc/shadowsocks-rust/config.json
    （eth 和 ipv4 由防火墙步骤和打印步骤各自使用，此处不需要）
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 4：配置 shadowsocks-rust")
    info("══════════════════════════════════════════════════════")

    ipv6_enabled = bool(ipv6)
    nameserver   = _get_nameserver(ipv6_enabled)
    password     = _generate_password()
    _write_ss_config(nameserver, password, mtu, ipv6_enabled)

    ok("步骤 4 完成\n")
    return password


def _get_nameserver(ipv6_enabled):
    if ipv6_enabled:
        ns = "8.8.8.8,8.8.4.4,2001:4860:4860::8888,2001:4860:4860::8844"
    else:
        ns = "8.8.8.8,8.8.4.4"
    info(f"DNS 服务器：{ns}")
    return ns


def _generate_password():
    info("使用 ssservice genkey 生成密码 ...")
    r = run_shell(
        f'ssservice genkey -m "{METHOD}"',
        capture=True, check=False,
    )
    password = r.stdout.strip()
    if not password:
        die(
            "ssservice genkey 失败，"
            "请确认 shadowsocks-rust 已正确安装到 /usr/local/bin"
        )
    ok(f"密码生成成功：{password}")
    return password

def _write_ss_config(nameserver, password, mtu, ipv6_first):
    try:
        SS_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        die(f"无法创建配置目录 {SS_CONFIG_DIR}：{e}")

    # server 字段：有 IPv6 时使用数组，否则使用字符串
    server_value = '["[::0]","0.0.0.0"]' if ipv6_first else '"0.0.0.0"'

    config_raw = f"""\
{{
    "server":{server_value},
    "server_port":{PORT},
    "password":"{password}",
    "method":"{METHOD}",
    "timeout":1800,
    "udp_timeout":1800,
    "plugin":"{OBFS_BIN}",
    "plugin_opts":"obfs={OBFS};obfs-host={OBFS_HOST};obfs-uri={OBFS_URI}",
    "fast_open":true,
    "reuse_port":true,
    "nofile":512000,
    "nameserver":"{nameserver}",
    "dscp":"EF",
    "mode":"tcp_and_udp",
    "mtu":{mtu},
    "mptcp":false,
    "ipv6_first":{"true" if ipv6_first else "false"},
    "use_syslog":true,
    "no_delay":true
}}
"""
    try:
        SS_CONFIG_FILE.write_text(config_raw, encoding="utf-8")
        ok(f"配置文件已写入：{SS_CONFIG_FILE}")
    except Exception as e:
        die(f"写入配置文件失败：{e}")

# ══════════════════════════════════════════════
#  步骤 5 — 配置内核参数
# ══════════════════════════════════════════════

def step_5_configure_kernel(ipv6=False):
    """
    步骤 5：配置内核参数
    - 检测 # Twist 标识，已存在则跳过 sysctl 追加
    - 配置资源限制 /etc/security/limits.conf
    - 配置 DNS /etc/resolv.conf
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 5：配置内核参数")
    info("══════════════════════════════════════════════════════")

    _apply_sysctl_params()
    _configure_limits()
    _configure_dns(ipv6)

    ok("步骤 5 完成\n")


def _apply_sysctl_params():
    sysctl_conf = Path("/etc/sysctl.conf")
    content = sysctl_conf.read_text(encoding="utf-8") if sysctl_conf.exists() else ""

    if "# Twist" in content:
        warn("检测到已存在 # Twist 标识，跳过重复写入")
        return

    sysctl_block = """
# Twist
fs.file-max = 512000
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.core.netdev_max_backlog = 256000
net.core.somaxconn = 4096
net.ipv4.udp_mem = 25600 51200 102400
net.ipv4.tcp_mem = 25600 51200 102400
net.ipv4.tcp_rmem = 4096 87380 67108864
net.ipv4.tcp_wmem = 4096 65536 67108864
net.ipv4.ip_local_port_range = 49152 65535
net.ipv4.tcp_max_syn_backlog = 8192
net.ipv4.tcp_max_tw_buckets = 4096
net.core.default_qdisc = fq
net.ipv4.ip_forward = 1
net.ipv4.tcp_window_scaling = 1
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_timestamps = 1
net.ipv4.tcp_fack = 1
net.ipv4.tcp_sack = 1
net.ipv4.tcp_dsack = 1
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fwmark_accept = 1
net.ipv4.tcp_stdurg = 1
net.ipv4.tcp_synack_retries = 30
net.ipv4.tcp_syn_retries = 30
net.ipv4.tcp_rfc1337 = 1
net.ipv4.tcp_fin_timeout = 60
net.ipv4.tcp_keepalive_time = 1800
net.ipv4.tcp_mtu_probing = 2
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_low_latency = 1
net.ipv4.udp_l3mdev_accept = 1
net.ipv4.fib_multipath_hash_policy = 1
net.ipv4.fib_multipath_use_neigh = 1
net.ipv4.cipso_rbm_optfmt = 1
net.ipv4.fwmark_reflect = 1
net.ipv4.conf.all.accept_source_route = 1
net.ipv4.conf.all.accept_redirects = 1
net.ipv4.conf.all.send_redirects = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.all.arp_accept = 1
net.ipv4.conf.all.arp_announce = 1
net.ipv4.conf.all.proxy_arp = 1
net.ipv4.conf.all.proxy_arp_pvlan = 1
net.ipv4.conf.all.mc_forwarding = 1
net.ipv6.conf.all.forwarding = 1
net.ipv6.conf.all.accept_source_route = 1
net.ipv6.conf.all.accept_redirects = 1
net.ipv6.conf.all.autoconf = 1
net.ipv6.conf.all.accept_ra = 2
net.ipv6.conf.all.seg6_enabled = 1

"""
    try:
        with open(sysctl_conf, "a", encoding="utf-8") as f:
            f.write(sysctl_block)
        ok("sysctl 参数已追加到 /etc/sysctl.conf")
    except Exception as e:
        die(f"写入 /etc/sysctl.conf 失败：{e}")

def _configure_limits():
    limits_conf = Path("/etc/security/limits.conf")
    backup(limits_conf)

    content = limits_conf.read_text(encoding="utf-8") if limits_conf.exists() else ""
    if "512000" in content:
        warn("limits.conf 中已存在 512000 配置，跳过")
        return

    try:
        with open(limits_conf, "a", encoding="utf-8") as f:
            f.write("*                soft    nofile          512000\n")
            f.write("*                hard    nofile          512000\n")
            f.write("\n")
        ok("资源限制已写入 /etc/security/limits.conf")
    except Exception as e:
        die(f"写入 /etc/security/limits.conf 失败：{e}")

def _configure_dns(ipv6=False):
    resolv = Path("/etc/resolv.conf")
    try:
        with open(resolv, "w", encoding="utf-8") as f:
            f.write("nameserver 8.8.8.8\n")
            f.write("nameserver 8.8.8.4\n")
            if ipv6:
                f.write("nameserver 2001:4860:4860::8888\n")
                f.write("nameserver 2001:4860:4860::8844\n")
            f.write("\n")
        ok("DNS 配置已写入 /etc/resolv.conf")
    except Exception as e:
        die(f"写入 /etc/resolv.conf 失败：{e}")

# ══════════════════════════════════════════════
#  步骤 6 — 配置防火墙（UFW）
# ══════════════════════════════════════════════

def step_6_configure_firewall(eth):
    """
    步骤 6：配置防火墙（UFW）
    - 开放 SSH / 80 / 443 / PORT（TCP+UDP）
    - 修改 /etc/ufw/sysctl.conf 开启转发
    - 修改 /etc/ufw/before.rules 插入 NAT + TCPMSS
    - 启用 UFW 并设置开机自启
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 6：配置防火墙（UFW）")
    info("══════════════════════════════════════════════════════")

    if not shutil.which("ufw"):
        die("未找到 ufw 命令，请确认已正确安装（apt install ufw）")

    _ufw_allow_ports()
    _ufw_sysctl_conf()
    _ufw_before_rules(eth)
    _ufw_enable()

    ok("步骤 6 完成\n")

def get_ssh_port():
    config = "/etc/ssh/sshd_config"
    port = 22  # 默认端口
    try:
        with open(config, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("Port "):
                    port = int(line.split()[1])
    except Exception:
        pass
    return port

def _ufw_allow_ports():

    rules = [
        (str(get_ssh_port()), "tcp", "SSH"),
        ("80",                "tcp", "HTTP TCP"),
        ("80",                "udp", "HTTP UDP"),
        ("443",               "tcp", "HTTPS TCP"),
        ("443",               "udp", "HTTPS UDP"),
        (str(PORT),           "tcp", f"Shadowsocks {PORT} TCP"),
        (str(PORT),           "udp", f"Shadowsocks {PORT} UDP"),
    ]
    for port, proto, desc in rules:
        r = run(f"ufw allow {port}/{proto}", check=False)
        if r.returncode != 0:
            die(f"UFW 规则添加失败：{desc}（{port}/{proto}）")
        ok(f"UFW：允许 {desc}（{port}/{proto}）")

def _ufw_sysctl_conf():
    ufw_sysctl = Path("/etc/ufw/sysctl.conf")
    if not ufw_sysctl.exists():
        warn(f"{ufw_sysctl} 不存在，跳过")
        return

    try:
        content = ufw_sysctl.read_text(encoding="utf-8")
        additions = {
            "net/ipv4/ip_forward"          : "net/ipv4/ip_forward=1",
            "net/ipv6/conf/all/forwarding" : "net/ipv6/conf/all/forwarding=1",
        }
        for key, line in additions.items():
            if re.search(rf"^#?\s*{re.escape(key)}", content, re.MULTILINE):
                content = re.sub(
                    rf"^#?\s*{re.escape(key)}.*$",
                    line,
                    content,
                    flags=re.MULTILINE,
                )
            else:
                content += f"\n{line}\n"
        ufw_sysctl.write_text(content, encoding="utf-8")
        ok(f"UFW sysctl.conf 已更新：{ufw_sysctl}")
    except Exception as e:
        die(f"修改 {ufw_sysctl} 失败：{e}")

def _ufw_before_rules(eth):
    before_rules = Path("/etc/ufw/before.rules")
    if not before_rules.exists():
        die(f"{before_rules} 不存在，UFW 安装可能不完整")

    try:
        content = before_rules.read_text(encoding="utf-8")

        # ── 1. 在文件最开头插入 NAT 块（*filter 之前）──
        nat_block = (
            f"# NAT 规则\n"
            f"*nat\n"
            f":POSTROUTING ACCEPT [0:0]\n"
            f"-A POSTROUTING -o {eth} -j MASQUERADE\n"
            f"COMMIT\n\n"
        )
        if "*nat" not in content:
            content = nat_block + content
            ok("UFW before.rules：NAT 规则已插入")
        else:
            warn("UFW before.rules：已存在 *nat 块，跳过")

        # ── 2. 在 *filter 块的第一个 COMMIT 前插入 TCPMSS ──
        tcpmss_rule = (
            "-A FORWARD -p tcp --tcp-flags SYN,RST SYN "
            "-j TCPMSS --clamp-mss-to-pmtu"
        )
        if tcpmss_rule not in content:
            lines     = content.split("\n")
            new_lines = []
            in_filter = False
            inserted  = False
            for line in lines:
                if line.strip() == "*filter":
                    in_filter = True
                if in_filter and not inserted and line.strip() == "COMMIT":
                    new_lines.append(tcpmss_rule)
                    inserted = True
                new_lines.append(line)
            content = "\n".join(new_lines)
            if inserted:
                ok("UFW before.rules：TCPMSS 规则已插入")
            else:
                warn("UFW before.rules：未找到 *filter COMMIT，TCPMSS 规则未插入")
        else:
            warn("UFW before.rules：TCPMSS 规则已存在，跳过")

        before_rules.write_text(content, encoding="utf-8")
    except Exception as e:
        die(f"修改 {before_rules} 失败：{e}")

def _ufw_enable():
    r = run_shell("echo 'y' | ufw enable", check=False)
    if r.returncode != 0:
        die("UFW 启用失败")
    ok("UFW 已启用")

    r2 = run("systemctl enable --now ufw", check=False)
    if r2.returncode != 0:
        die("UFW 设置开机自启失败")
    ok("UFW 已设置开机自启")

    run("ufw status verbose", check=False)

# ══════════════════════════════════════════════
#  步骤 7 — 开机启动（systemd）
# ══════════════════════════════════════════════

def step_7_setup_autostart():
    """
    步骤 7：配置开机启动
    - systemctl daemon-reload
    - systemd enable --now 各服务
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 7：配置开机启动")
    info("══════════════════════════════════════════════════════")

    _systemd_enable_services()
    ok("步骤 7 完成\n")


def _systemd_enable_services():
    r = run("systemctl daemon-reload", check=False)
    if r.returncode != 0:
        die("systemctl daemon-reload 失败")
    ok("systemd daemon-reload 完成")

    services = ["shadowsocks-rust", "nginx", "fail2ban", "cron"]
    for svc in services:
        r = run(f"systemctl enable --now {svc}", check=False)
        if r.returncode != 0:
            warn(f"systemd：{svc} 启用失败（服务可能不存在或启动出错）")
        else:
            ok(f"systemd：{svc} 已启用并启动")

# ══════════════════════════════════════════════
#  步骤 8 — 配置 Nginx
# ══════════════════════════════════════════════

def step_8_configure_nginx():
    """
    步骤 8：配置 Nginx
    - 写入 /etc/nginx/sites-enabled/default（伪装微软）
    - nginx -t 测试，失败则退出
    - systemctl restart nginx
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 8：配置 Nginx")
    info("══════════════════════════════════════════════════════")

    _write_nginx_config()
    _reload_nginx()

    ok("步骤 8 完成\n")


def _write_nginx_config():
    nginx_default = Path("/etc/nginx/sites-enabled/default")
    backup(nginx_default)

    nginx_conf = """\
server {
    listen 80;
    server_name _;
    location / {
        return 301 http://microsoft.com$request_uri;
    }
}
"""
    try:
        nginx_default.write_text(nginx_conf, encoding="utf-8")
        ok(f"Nginx 配置已写入：{nginx_default}")
    except Exception as e:
        die(f"写入 Nginx 配置失败：{e}")

def _reload_nginx():
    r = run("nginx -t", check=False, capture=True)
    if r.returncode != 0:
        die(f"Nginx 配置测试失败，请检查配置：\n{r.stderr}")
    ok("Nginx 配置测试通过")

    r2 = run("systemctl restart nginx", check=False)
    if r2.returncode != 0:
        die("Nginx 重启失败")
    ok("Nginx 已重启")

# ══════════════════════════════════════════════
#  步骤 9 — 配置 fail2ban
# ══════════════════════════════════════════════

def step_9_configure_fail2ban():
    """
    步骤 9：配置 fail2ban
    - 写入过滤器 nginx-badurl.conf（参考用）
    - 写入 jail /etc/fail2ban/jail.d/nginx-all.local
    - 重启 fail2ban 并验证
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 9：配置 fail2ban")
    info("══════════════════════════════════════════════════════")

    _write_fail2ban_filter()
    _write_fail2ban_jail()
    _reload_fail2ban()

    ok("步骤 9 完成\n")

def _write_fail2ban_filter():
    filter_file = Path("/etc/fail2ban/filter.d/nginx-badurl.conf")
    if filter_file.exists():
        warn(f"{filter_file} 已存在，跳过")
        return
    try:
        filter_file.write_text(
            '[Definition]\nfailregex = <HOST> -.*"(GET|POST|HEAD) (/admin.*)\n',
            encoding="utf-8",
        )
        ok(f"fail2ban 过滤器已写入：{filter_file}")
    except Exception as e:
        die(f"写入 fail2ban 过滤器失败：{e}")

def _write_fail2ban_jail():
    jail_dir  = Path("/etc/fail2ban/jail.d")
    jail_dir.mkdir(parents=True, exist_ok=True)
    jail_file = jail_dir / "nginx-all.local"

    if jail_file.exists():
        backup(jail_file)

    jail_conf = """\
[nginx-bad-request]
enabled  = true
filter   = nginx-bad-request
port     = http,https
logpath  = /var/log/nginx/access.log
backend  = auto
maxretry = 1
bantime = 86400
findtime = 600

[nginx-botsearch]
enabled  = true
filter   = nginx-botsearch
port     = http,https
logpath  = /var/log/nginx/access.log
backend  = auto
maxretry = 1
bantime = 86400
findtime = 600

[nginx-http-auth]
enabled  = true
filter   = nginx-http-auth
port     = http,https
logpath  = /var/log/nginx/error.log
backend  = auto
maxretry = 1
bantime = 86400
findtime = 600

[nginx-limit-req]
enabled  = true
filter   = nginx-limit-req
port     = http,https
logpath  = /var/log/nginx/error.log
backend  = auto
maxretry = 1
bantime = 86400
findtime = 600
"""
    try:
        jail_file.write_text(jail_conf, encoding="utf-8")
        ok(f"fail2ban jail 配置已写入：{jail_file}")
    except Exception as e:
        die(f"写入 fail2ban jail 配置失败：{e}")

def _reload_fail2ban():
    r = run("systemctl restart fail2ban", check=False)
    if r.returncode != 0:
        die("fail2ban 重启失败，请检查配置")
    ok("fail2ban 已重启")
    run("fail2ban-client status", check=False)
    run("fail2ban-client status nginx-bad-request", check=False)

# ══════════════════════════════════════════════
#  步骤 10 — 打印连接信息（最后一步）
# ══════════════════════════════════════════════

def step_10_print_output(ipv4, ipv6, password):
    """
    步骤 10：打印连接信息（最后一步）
    - 生成 ss:// 链接
    - 终端打印二维码（python3-qrcode）
    - 彩色打印各项参数
    """
    info("══════════════════════════════════════════════════════")
    info("步骤 10：打印连接信息")
    info("══════════════════════════════════════════════════════")

    public_ip    = ipv4 if ipv4 else "<your-server-ip>"
    public_ipv6  = ipv6 if ipv6 else ""
    ipv6_enabled = bool(ipv6)

    _print_ss_info(public_ip, public_ipv6, password, ipv6_enabled)

def _build_ss_link(public_ip, password):
    base64_str = base64.b64encode(
        f"{METHOD}:{password}".encode()
    ).decode().rstrip("=")
    ss_link = (
        f"ss://{base64_str}@{public_ip}:{PORT}"
        f"?plugin=obfs-local"
        f";obfs={OBFS}"
        f";obfs-host={OBFS_HOST}"
        f";obfs-uri={OBFS_URI}"
        f"#Twist"
    )
    return ss_link, base64_str

def _print_ss_info(public_ip, public_ipv6, password, ipv6_enabled):
    ss_link, base64_str = _build_ss_link(public_ip, password)

    # 二维码
    _print_qr(ss_link)

    # ss:// 链接（彩色）
    link_body = (
        f"{base64_str}@{public_ip}:{PORT}"
        f"?plugin=obfs-local"
        f";obfs={OBFS}"
        f";obfs-host={OBFS_HOST}"
        f";obfs-uri={OBFS_URI}"
        f"#Twist"
    )
    print(f"# [\033[32;1mss://\033[0m\033[34;1m{link_body}\033[0m]")

    # 各项参数（彩色）
    line = f"# [\033[32;1mServer IP:\033[0m \033[34;1m{public_ip}\033[0m"
    if ipv6_enabled and public_ipv6:
        line += f"(\033[34;1m{public_ipv6}\033[0m)"
    line += (
        f" \033[32;1mPassWord:\033[0m \033[34;1m{password}\033[0m"
        f" \033[32;1mEncryption:\033[0m \033[34;1m{METHOD}\033[0m"
        f" \033[32;1mOBFS:\033[0m \033[34;1m{OBFS}\033[0m"
        f" \033[32;1mOBFS-HOST:\033[0m \033[34;1m{OBFS_HOST}\033[0m"
        f" \033[32;1mOBFS-URI:\033[0m \033[34;1m{OBFS_URI}\033[0m]"
    )
    print(line)

def _print_qr(data):
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(data)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
        ok("二维码已生成")
    except ImportError:
        warn("python3-qrcode 未安装，跳过二维码生成")
        info(f"SS 链接：{data}")

# ══════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════

def main():
    global SS_ARCH
    SS_ARCH = _detect_arch()   # 在任何步骤之前确定架构

    print()
    print("\033[1;36m" + "═" * 55)
    print("  Shadowsocks-Rust Installer for Ubuntu Server")
    print(f"  架构        : {SS_ARCH}")
    print( "  Script Date : 2026-04-29")
    print("═" * 55 + "\033[0m\n")

    eth, ipv4, ipv6, mtu = step_1_prerequisites()
    step_2_enable_bbr()
    step_3_install_shadowsocks()
    step_3_1_create_service()
    step_3_2_install_simple_obfs()
    password = step_4_configure_shadowsocks(ipv6, mtu)
    step_5_configure_kernel(bool(ipv6))
    step_6_configure_firewall(eth)
    step_7_setup_autostart()
    step_8_configure_nginx()
    step_9_configure_fail2ban()
    step_10_print_output(ipv4, ipv6, password)

    print()
    print("\033[1;32m" + "═" * 55)
    print("  全部安装完成！请重启服务器使所有配置生效。")
    print("  注意：国内电信/联通 DPI 检测严格，混淆可能失效。")
    print("═" * 55 + "\033[0m\n")

    if prompt("是否立即重启服务器？(y/n)", "n").lower() == "y":
        info("5 秒后重启 ...")
        import time
        time.sleep(5)
        run("reboot")

if __name__ == "__main__":
    main()