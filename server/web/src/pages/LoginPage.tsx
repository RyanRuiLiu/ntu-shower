import { Button } from "@heroui/react";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { ApiError } from "../api/client";
import { sessionActions } from "../session";

/** 登录页：口令换签名 Cookie；成功后回到来源路径。 */
export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!username || !password) {
      setError("请填写用户名与口令");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await sessionActions.login(username, password);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== "/login" ? from : "/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "登录失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center">
      <form
        className="glass-panel w-96 p-8"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <h1 className="mb-1 text-xl font-semibold">NTU Shower 管理台</h1>
        <p className="mb-6 text-sm text-default-500">登录后使用管理面功能</p>
        <input
          className="mb-3 w-full rounded-xl border border-white/10 bg-content2/60 px-4 py-2.5 text-sm outline-none focus:border-indigo-400/50"
          placeholder="用户名"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
        />
        <input
          className="mb-2 w-full rounded-xl border border-white/10 bg-content2/60 px-4 py-2.5 text-sm outline-none focus:border-indigo-400/50"
          placeholder="口令"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
        />
        {error && <p className="mb-2 text-sm text-danger">{error}</p>}
        <Button variant="primary" className="mt-3 w-full" isPending={busy} type="submit">
          登录
        </Button>
      </form>
    </div>
  );
}
