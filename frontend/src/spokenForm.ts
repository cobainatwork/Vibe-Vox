// 管理平面：預覽一段文字經前處理層之後的樣子（/api/admin/tts，{data} 信封）。
//
// **不是送進模型的字串**：adapter 之下還有一層字形轉簡（#51），那一層是 VoxCPM2 的輸入
// 格式要求。這裡刻意停在轉換之前，操作者要讀得懂這段文字才做得了下面那個判斷。
//
// TN 前處理層唸錯不會回錯誤碼也不進 log，操作者只聽到「唸錯了」而分不出是前處理錯了還是
// 模型錯了。這個呼叫把那個字串攤出來，那個判斷才做得到。
//
// **不在前端重做一次 TN。** 規則有數十條且會逐條增加，複製一份到 TypeScript 等於保證兩邊
// 遲早不一致，而不一致的那一刻預覽就開始說謊。

export async function previewSpokenForm(
  input: string,
  instruct?: string,
): Promise<string> {
  const resp = await fetch("/api/admin/tts/spoken-form", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, instruct }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => null);
    throw new Error(body?.error?.message ?? `預覽失敗：HTTP ${resp.status}`);
  }
  const body = (await resp.json()) as { data: { spoken: string } };
  return body.data.spoken;
}
