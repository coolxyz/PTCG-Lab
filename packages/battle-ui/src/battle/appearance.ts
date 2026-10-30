import type { CSSProperties } from "react";

const MAX_IMAGE_LENGTH = 1_500_000;
export const PLAYMATS = [
  { id: "lavender", name: "大师球浅紫", color: "#eee5f8", light: true },
  { id: "slate", name: "岩灰", color: "#273444" },
  { id: "forest", name: "墨绿", color: "#21483c" },
  { id: "navy", name: "深蓝", color: "#193653" },
  { id: "plum", name: "暗紫", color: "#44344e" },
  { id: "sand", name: "暖沙", color: "#d8cfb9", light: true },
  {
    id: "classic",
    name: "经典赛场",
    color: "#163f3c",
    image: new URL("./playmats/classic.svg", import.meta.url).href,
  },
  {
    id: "stadium",
    name: "红蓝对阵",
    color: "#222e48",
    image: new URL("./playmats/stadium.svg", import.meta.url).href,
  },
  {
    id: "circuit",
    name: "蓝色竞技场",
    color: "#173650",
    image: new URL("./playmats/circuit.svg", import.meta.url).href,
  },
] as const;
export type Appearance = { preset: string; customImage?: string };
export type AppearanceStorage = {
  load: () => Appearance;
  save: (value: Appearance) => void;
};
export const DEFAULT_APPEARANCE: Appearance = { preset: "lavender" };

function safeImage(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= MAX_IMAGE_LENGTH &&
    /^data:image\/(jpeg|png|webp);base64,[A-Za-z0-9+/]+=*$/.test(value)
  );
}

export function parseAppearance(raw: string | null): Appearance {
  try {
    const value = JSON.parse(raw || "null");
    if (!value || typeof value !== "object") return DEFAULT_APPEARANCE;
    const customImage = safeImage(value.customImage)
      ? value.customImage
      : undefined;
    if (
      PLAYMATS.some((mat) => mat.id === value.preset) ||
      (value.preset === "custom" && customImage)
    )
      return { preset: value.preset, customImage };
  } catch {
    /* Restricted storage and stale preferences use the default. */
  }
  return DEFAULT_APPEARANCE;
}

export function appearanceStyle(value: Appearance): CSSProperties {
  const mat = PLAYMATS.find((item) => item.id === value.preset) || PLAYMATS[0];
  const custom = value.preset === "custom" && safeImage(value.customImage);
  const image = custom
    ? value.customImage
    : "image" in mat
      ? mat.image
      : undefined;
  const light = !custom && "light" in mat && mat.light;
  return {
    "--arena-background": image
      ? `${custom ? "linear-gradient(#101c30b3, #101c30b3), " : ""}url("${image}") center / cover no-repeat ${mat.color}`
      : mat.color,
    "--arena-ink": light ? "#352b46" : "#f3f7fa",
    "--arena-muted": light ? "#6e5c80" : "#d7e2e9",
    "--arena-score-self": light ? "#68449e" : "#9fe7ff",
    "--arena-score-other": light ? "#a43f7f" : "#ffd1b9",
    "--arena-zone": light ? "#ffffff66" : "#07152266",
    "--arena-line": light ? "#c2add9" : "#adc6d366",
  } as CSSProperties;
}

/** Decode and re-encode local raster files; never send user artwork to an API. */
export async function importPlaymat(file: File): Promise<string> {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type))
    throw new Error("请选择 JPG、PNG 或 WebP 图片。");
  if (file.size > 5 * 1024 * 1024) throw new Error("图片不能超过 5 MB。");
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error("无法读取这张图片，请换一张有效的图片。");
  }
  try {
    if (bitmap.width * bitmap.height > 32_000_000)
      throw new Error("图片像素过大，请缩小到 3200 万像素以内。");
    const scale = Math.min(1, 1600 / bitmap.width, 1200 / bitmap.height);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("当前浏览器无法处理图片。");
    context.fillStyle = "#273444";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const result = canvas.toDataURL("image/jpeg", 0.82);
    if (!safeImage(result))
      throw new Error("处理后的图片仍然过大，请选择更简单或更小的图片。");
    return result;
  } finally {
    bitmap.close();
  }
}
