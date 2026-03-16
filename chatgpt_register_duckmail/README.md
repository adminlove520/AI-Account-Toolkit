# ChatGPT 批量自动注册工具

> 使用 DuckMail 临时邮箱，并发自动注册 ChatGPT 账号

## 功能

- 📨 自动创建临时邮箱 (DuckMail)
- 📥 自动获取 OTP 验证码
- ⚡ 支持并发注册多个账号
- 🔄 自动处理 OAuth 登录
- ☁️ 支持代理配置
- 📤 支持上传账号到 Codex / CPA 面板

## 环境

```bash
pip install curl_cffi
```

## 配置 (config.json)

```json
{
  "total_accounts": 5,
  "duckmail_api_base": "https://api.duckmail.sbs",
  "duckmail_bearer": "你的 DuckMail API Token",
  "proxy": "http://127.0.0.1:7890",
  "output_file": "registered_accounts.txt",
  "enable_oauth": true,
  "oauth_redirect_uri": "http://localhost:1455/auth/callback",
  "ak_file": "ak.txt",
  "rk_file": "rk.txt"
}
```

| 配置项 | 说明 |
|--------|------|
| total_accounts | 注册账号数量 |
| duckmail_bearer | DuckMail API Token |
| proxy | 代理地址 (可选) |
| output_file | 输出账号文件 |
| enable_oauth | 启用 OAuth 登录 |
| ak_file | Access Key 文件 |
| rk_file | Refresh Key 文件 |

## CPA 面板集成

注册完成后，可以自动上传账号到 CPA 面板：

| 配置项 | 说明 | 参考 |
|--------|------|------|
| upload_api_url | CPA 面板上传 API 地址 | https://help.router-for.me/cn/ |
| upload_api_token | CPA 面板登录密码 | 你的 CPA 面板密码 |

> CPA 面板仓库: https://github.com/dongshuyan/CPA-Dashboard

## 使用

```bash
python chatgpt_register.py
```

## 输出

注册成功的账号会保存到 `registered_accounts.txt`

## 目录结构

```
chatgpt_register/
├── chatgpt_register.py    # 主程序
├── config.json             # 配置文件
├── README.md               # 本文档
├── codex/                  # Codex 协议密钥生成
│   ├── config.json
│   └── protocol_keygen.py
├── registered_accounts.txt # 输出的账号
├── ak.txt                  # Access Keys
└── rk.txt                 # Refresh Keys
```

## 注意事项

- 需要有效的代理才能注册成功
- DuckMail API Token 需要从 https://duckmail.sbs 获取
- 建议使用代理避免 IP 被封
- 使用 CPA 面板需要先部署面板服务
