import type { ComponentProps } from "react";
import { Battle } from "./Battle";

/** The default build retains the frozen product controller for rollback. */
export function PracticeApp({
  active,
  ...props
}: ComponentProps<typeof Battle> & {
  active: boolean;
}) {
  return active ? <Battle {...props} /> : null;
}
