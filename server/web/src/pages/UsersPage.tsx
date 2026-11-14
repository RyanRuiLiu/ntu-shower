import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@heroui/react";
import { useState } from "react";

import { usersApi } from "../api/queries";
import { ApiError } from "../api/client";
import { fmtFullDateTime } from "../format";
import type { KeyIssued, KeyView } from "../api/types";

/** 用户与密钥页：名单 + 每用户密钥子表（签发明文一次性、编辑即生效、吊销单枚）。 */
export function UsersPage() {
  const queryClient = useQueryClient();
  const catalog = useQuery({ queryKey: ["users"], queryFn: usersApi.catalog });
  const [openUser, setOpenUser] = useState<string | null>(null);

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["users"] });

  return (
    <div className="grid gap-3">
      <section className="glass-panel p-4">
        <h2 className="mb-3 text-sm font-semibold">用户名单</h2>
        <div className="grid gap-2">
          {(catalog.data?.users ?? []).map((user) => (
            <div key={user.username} className="rounded-xl bg-content2/40 p-3">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm">{user.username}</span>
                <span className="text-sm">{user.name}</span>
                <span
                  className={`ml-auto rounded-full px-2 py-0.5 text-xs ${
                    user.disabled ? "bg-rose-500/20 text-rose-300" : "bg-emerald-500/20 text-emerald-300"
                  }`}
                >
                  {user.disabled ? "已禁用" : "在用"}
                </span>
                <span className="text-xs text-default-500">
                  {user.has_password ? "可登录" : "仅密钥"} · 最近登录 {fmtFullDateTime(user.last_login_at)}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onPress={() => setOpenUser(openUser === user.username ? null : user.username)}
                >
                  {openUser === user.username ? "收起" : "密钥"}
                </Button>
              </div>
              {openUser === user.username && (
                <KeySection username={user.username} catalog={catalog.data} onDone={invalidate} />
              )}
            </div>
          ))}
        </div>
      </section>

      <CreateUserForm onDone={invalidate} />
    </div>
  );
}

function CreateUserForm({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const create = useMutation({
    mutationFn: usersApi.create,
    onSuccess: () => {
      onDone();
      setUsername("");
      setName("");
      setPassword("");
    },
    onError: (error) => setError(error instanceof ApiError ? error.message : "登记失败"),
  });

  return (
    <section className="glass-panel p-4">
      <h2 className="mb-3 text-sm font-semibold">登记用户</h2>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate({ username, name, password: password || undefined });
        }}
      >
        <input
          className="w-44 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm font-mono"
          placeholder="用户名（唯一）"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
        />
        <input
          className="w-44 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
          placeholder="显示名"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <input
          className="w-44 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
          placeholder="口令（留空=仅密钥）"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <Button size="sm" variant="primary" isPending={create.isPending} type="submit">
          登记
        </Button>
      </form>
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </section>
  );
}

function KeySection({
  username,
  catalog,
  onDone,
}: {
  username: string;
  catalog: Awaited<ReturnType<typeof usersApi.catalog>> | undefined;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const keys = useQuery({ queryKey: ["users", username, "keys"], queryFn: () => usersApi.keys(username) });
  const [issued, setIssued] = useState<string>("");
  const [editingKey, setEditingKey] = useState<number | null>(null);
  const [keyName, setKeyName] = useState("");
  const [scopes, setScopes] = useState<string[]>([]);
  const [error, setError] = useState("");

  const submitKey = useMutation({
    mutationFn: (value: { name: string; scopes: string[]; device_scope: string[] }) =>
      editingKey === null
        ? usersApi.createKey(username, value)
        : usersApi.updateKey(username, editingKey, value),
    onSuccess: (result) => {
      const view = result as KeyView;
      if ("token" in result && (result as KeyIssued).token) setIssued((result as KeyIssued).token);
      else if (view) setIssued("");
      setEditingKey(null);
      setScopes([]);
      onDone();
      void queryClient.invalidateQueries({ queryKey: ["users", username, "keys"] });
    },
    onError: (error) => setError(error instanceof ApiError ? error.message : "操作失败"),
  });

  const revoke = useMutation({
    mutationFn: (keyId: number) => usersApi.revokeKey(username, keyId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["users", username, "keys"] }),
  });

  const scopeList = catalog?.scopes ?? [];

  const toggleScope = (scope: string) =>
    setScopes((current) =>
      current.includes(scope) ? current.filter((item) => item !== scope) : [...current, scope],
    );

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    submitKey.mutate({
      name: String(form.get("name") ?? ""),
      scopes,
      device_scope: ["*"],
    });
  };

  return (
    <div className="mt-3 border-t border-white/5 pt-3">
      {(keys.data?.keys ?? []).map((key) => (
        <div key={key.id} className="mb-1 flex flex-wrap items-center gap-2 text-xs">
          <span className="font-medium">{key.name}</span>
          {key.scopes.map((scope) => (
            <span key={scope} className="rounded-full bg-indigo-500/20 px-2 py-0.5 text-indigo-200">
              {scope}
            </span>
          ))}
          <span className="font-mono text-default-400">{key.token_hint}</span>
          <span className="text-default-500">最后使用 {fmtFullDateTime(key.last_used_at)}</span>
          {key.revoked_at ? (
            <span className="rounded-full bg-rose-500/20 px-2 py-0.5 text-rose-300">已吊销</span>
          ) : (
            <>
              <Button
                size="sm"
                variant="ghost"
                onPress={() => {
                  setEditingKey(key.id);
                  setKeyName(key.name);
                  setScopes(key.scopes);
                }}
              >
                编辑
              </Button>
              <Button size="sm" variant="danger-soft" onPress={() => revoke.mutate(key.id)}>
                吊销
              </Button>
            </>
          )}
        </div>
      ))}

      <form className="mt-3" onSubmit={submit}>
        <div className="mb-2 flex flex-wrap gap-3">
          {scopeList.map((option) => (
            <label key={option.scope} className="flex items-center gap-1.5 text-xs">
              <input
                type="checkbox"
                checked={scopes.includes(option.scope)}
                onChange={() => toggleScope(option.scope)}
              />
              {option.label}
            </label>
          ))}
        </div>
        <input
          className="mr-2 w-44 rounded-xl border border-white/10 bg-content2/60 px-3 py-1.5 text-sm"
          placeholder="密钥名称"
          name="name"
          value={keyName}
          onChange={(event) => setKeyName(event.target.value)}
        />
        <Button size="sm" variant="primary" isPending={submitKey.isPending} type="submit">
          {editingKey === null ? "签发密钥" : "保存修改"}
        </Button>
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
      </form>

      {issued && (
        <div className="mt-2 rounded-xl bg-emerald-500/10 p-3">
          <p className="mb-1 text-xs text-emerald-300">明文仅显示一次，请立即保存：</p>
          <code className="break-all font-mono text-xs">{issued}</code>
        </div>
      )}
    </div>
  );
}
