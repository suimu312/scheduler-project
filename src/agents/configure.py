"""本地隐藏输入 API Key，保存到 Windows 当前用户环境，不写入仓库。"""
import argparse
import getpass
import os


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("此配置器用于 Windows；其他平台请设置 ARK_API_KEY / ARK_MODEL 环境变量。")
    import winreg
    secret = getpass.getpass("Ark API Key (hidden): ").strip()
    if not secret:
        raise SystemExit("未输入密钥，未保存配置。")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        winreg.SetValueEx(key, "ARK_API_KEY", 0, winreg.REG_SZ, secret)
        winreg.SetValueEx(key, "ARK_MODEL", 0, winreg.REG_SZ, args.model)
    print("Saved model and API Key to Windows user environment; no key written to repository.")


if __name__ == "__main__":
    main()
