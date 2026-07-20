import { useEffect, useRef } from "react";

// 焦点是否在可输入元素里:决定 Esc 是"先让输入框失焦"还是"关闭遮罩",
// 也用于方向键导航时把按键让回给光标。
export function isTypingElement(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

// 模块级遮罩栈:Esc 只关最上层的那个遮罩(弹窗盖在抽屉上时先关弹窗)。
// 两段式:焦点在输入框里时,Esc 先让其失焦,不关闭。
const closers: Array<() => void> = [];

function handleKeydown(event: KeyboardEvent) {
  if (event.key !== "Escape" || closers.length === 0) return;
  const active = document.activeElement;
  if (isTypingElement(active)) {
    (active as HTMLElement).blur();
    return;
  }
  event.preventDefault();
  closers[closers.length - 1]();
}

function register(close: () => void): () => void {
  if (closers.length === 0) document.addEventListener("keydown", handleKeydown);
  closers.push(close);
  return () => {
    const index = closers.lastIndexOf(close);
    if (index !== -1) closers.splice(index, 1);
    if (closers.length === 0) document.removeEventListener("keydown", handleKeydown);
  };
}

// active 为真时把 onClose 压入遮罩栈,Esc 关最上层;active 转假或卸载时出栈。
// onClose 用 ref 兜住,避免每次渲染换引用导致反复进出栈。
export function useEscapeClose(active: boolean, onClose: () => void): void {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    if (!active) return;
    return register(() => onCloseRef.current());
  }, [active]);
}
