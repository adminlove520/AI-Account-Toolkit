const axios = require('axios');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

class OAuthService {
    constructor() {
        this.clientId = 'app_EMoamEEZ73f0CkXaXp7hrann';
        this.redirectPort = 1455;
        this.redirectUri = `http://localhost:${this.redirectPort}/auth/callback`;
        this.codeVerifier = null;
        this.codeChallenge = null;
        this.state = null;
        this.regeneratePKCE();
    }

    /**
     * 生成 Code Verifier
     */
    generateCodeVerifier() {
        return crypto.randomBytes(32).toString('base64url');
    }

    /**
     * 生成 Code Challenge
     */
    generateCodeChallenge(verifier) {
        return crypto.createHash('sha256').update(verifier).digest('base64url');
    }

    /**
     * 重新生成 PKCE 参数和 state
     */
    regeneratePKCE() {
        this.codeVerifier = this.generateCodeVerifier();
        this.codeChallenge = this.generateCodeChallenge(this.codeVerifier);
        this.state = crypto.randomBytes(16).toString('hex');
        console.log('[OAuth] 已重新生成 PKCE 参数和 state');
    }

    /**
     * 获取 OAuth 授权 URL
     * @returns {string} 授权 URL
     */
    getAuthUrl() {
        const params = new URLSearchParams({
            client_id: this.clientId,
            code_challenge: this.codeChallenge,
            code_challenge_method: 'S256',
            codex_cli_simplified_flow: 'true',
            id_token_add_organizations: 'true',
            prompt: 'login',
            redirect_uri: this.redirectUri,
            response_type: 'code',
            scope: 'openid email profile offline_access',
            state: this.state
        });
        return `https://auth.openai.com/oauth/authorize?${params.toString()}`;
    }

    /**
     * 从 localhost 回调 URL 中提取授权参数
     * @param {string} callbackUrl - 完整的回调 URL
     * @returns {object|null} 提取的参数对象
     */
    extractCallbackParams(callbackUrl) {
        try {
            const url = new URL(callbackUrl);
            const params = {
                code: url.searchParams.get('code'),
                state: url.searchParams.get('state'),
                error: url.searchParams.get('error'),
                error_description: url.searchParams.get('error_description')
            };

            // 验证 state
            if (params.state && params.state !== this.state) {
                console.error('[OAuth] State 不匹配:', params.state, '期望:', this.state);
                return null;
            }

            return params;
        } catch (e) {
            console.error('[OAuth] 解析回调 URL 失败:', e.message);
            return null;
        }
    }

    /**
     * 用授权码换取 Token
     * @param {string} code - 授权码
     * @param {string} email - 邮箱地址
     * @returns {Promise<object>} Token 对象
     */
    async exchangeTokenAndSave(code, email) {
        try {
            console.log('[OAuth] 开始用 code 换取 Token');

            const body = new URLSearchParams({
                grant_type: 'authorization_code',
                code: code,
                redirect_uri: this.redirectUri,
                client_id: this.clientId,
                code_verifier: this.codeVerifier
            }).toString();

            const response = await axios.post('https://auth.openai.com/oauth/token', body, {
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
            });

            const tokens = response.data;

            // 解析 JWT 获取 account_id
            let accountId = "";
            try {
                const payloadStr = Buffer.from(tokens.access_token.split('.')[1], 'base64').toString('utf8');
                const payload = JSON.parse(payloadStr);
                const apiAuth = payload['https://api.openai.com/auth'] || {};
                accountId = apiAuth.chatgpt_account_id || "";
            } catch (e) {
                console.error('[OAuth] 解析 access_token 获取 account_id 失败:', e.message);
            }

            const now = new Date();
            const expiredTime = new Date(now.getTime() + tokens.expires_in * 1000);

            const outData = {
                access_token: tokens.access_token,
                account_id: accountId,
                disabled: false,
                email: email,
                expired: expiredTime.toISOString().replace(/\.[0-9]{3}Z$/, '+08:00'),
                id_token: tokens.id_token,
                last_refresh: now.toISOString().replace(/\.[0-9]{3}Z$/, '+08:00'),
                refresh_token: tokens.refresh_token,
                type: 'codex'
            };

            // 保存到文件
            const outputDir = path.join(process.cwd(), 'tokens');
            if (!fs.existsSync(outputDir)) {
                fs.mkdirSync(outputDir, { recursive: true });
            }

            const filename = `token_${Date.now()}.json`;
            const filepath = path.join(outputDir, filename);
            fs.writeFileSync(filepath, JSON.stringify(outData, null, 2));

            console.log(`[OAuth] Token 成功保存至: ${filepath}`);
            return outData;
        } catch (error) {
            console.error('[OAuth] 换取 Token 失败:', error.response ? error.response.data : error.message);
            throw error;
        }
    }
}

module.exports = { OAuthService };
