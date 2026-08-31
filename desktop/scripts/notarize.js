/**
 * LocalFlow AI — Apple 公证钩子（electron-builder afterSign）
 *
 * 仅在 macOS 打包且提供了完整 Apple 凭据时执行公证；否则安全跳过，
 * 保证 CI 在未配置凭据时仍能正常出包（只不过该包无法绕过 Gatekeeper）。
 *
 * 需要的环境变量（建议放在 CI Secret，不要写进仓库）：
 *   - APPLE_ID                       Apple 开发者账号邮箱
 *   - APPLE_APP_SPECIFIC_PASSWORD     App 专用密码（appleid.apple.com 生成）
 *   - APPLE_TEAM_ID                  Apple Developer 团队 ID（10 位大写字母数字）
 *   - CSC_LINK                       指向 Developer ID 签名证书 p12 的 URL/路径（签名用）
 *   - CSC_KEY_PASSWORD               p12 的密码（签名用）
 *
 * 使用前还需在 electron-builder.yml 的 mac 段把：
 *   identity: null
 * 改为：
 *   identity: "Developer ID Application: <团队名> (<TEAM_ID>)"
 * 这样 electron-builder 会用 CSC_LINK 完成 Developer ID 签名，本钩子再对签名后的
 * .app 执行公证。公证通过后会收到 apple 回执，dmg 才可双击直接打开。
 */
const { notarize } = require('@electron/notarize');

exports.default = async function notarizing(context) {
  const { electronPlatformName, appOutDir } = context;

  // 非 macOS 打包直接跳过
  if (electronPlatformName !== 'darwin') {
    return;
  }

  const appleId = process.env.APPLE_ID;
  const applePassword = process.env.APPLE_APP_SPECIFIC_PASSWORD;
  const teamId = process.env.APPLE_TEAM_ID;
  const identity = process.env.CSC_LINK; // 未配置签名证书时视为跳过公证

  // 凭据不齐 → 跳过公证，但给明确提示，避免“静默失败”让发版人误以为已公证
  if (!appleId || !applePassword || !teamId || !identity) {
    console.warn(
      '\n[notarize] 跳过公证：缺少 Apple 凭据。\n' +
        '  需要 APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID / CSC_LINK。\n' +
        '  未公证的 dmg 在他人 Mac 上首次打开会被 Gatekeeper 拦截（需右键→打开 或 xattr 解除）。\n'
    );
    return;
  }

  const appName = context.packager.appInfo.productFilename;
  const appPath = `${appOutDir}/${appName}.app`;

  console.log(`\n[notarize] 开始公证 ${appPath} (team=${teamId}) ...`);
  try {
    await notarize({
      tool: 'notarytool', // 需要 Xcode 13+ / notarytool 可用环境
      appBundleId: 'ai.localflow.app',
      appleId,
      appleIdPassword: applePassword,
      teamId,
      appPath,
    });
    console.log('[notarize] 公证提交成功，等待 Apple 处理（通常 1–5 分钟，CI 会一直阻塞到出结果）。');
  } catch (err) {
    console.error('[notarize] 公证失败：', err);
    throw err; // 让打包流程失败，避免发布一个未公证的“假成功”包
  }
};
