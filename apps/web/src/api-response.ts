/** Keep HTTP failures readable even when a proxy/server returns plain text. */
export async function apiResponse(response: Response, path: string): Promise<any> {
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`数据服务返回了无效响应（${path}，HTTP ${response.status}），请重试；若持续出现，请提供此信息。`);
  }
  if (!response.ok) {
    throw new Error(
      data?.message || data?.detail?.[0]?.msg || `操作未完成（${path}，HTTP ${response.status}），请稍后重试`,
    );
  }
  return data;
}
