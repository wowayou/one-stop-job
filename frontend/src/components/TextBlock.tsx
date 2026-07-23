import { CheckCircle2, Copy } from "lucide-react";
import { useState } from "react";
import { copyToClipboard } from "../api";

export function TextBlock({ title, text }: { title: string; text: string }) {
  const [copied, setCopied] = useState(false);
  async function copyText() {
    if (await copyToClipboard(text)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } else {
      setCopied(false);
    }
  }
  return (
    <article className="text-block">
      <div className="text-block-head">
        <strong>{title}</strong>
        <button type="button" className="icon-button compact" title={`复制${title}`} onClick={copyText}>
          {copied ? <CheckCircle2 size={14} /> : <Copy size={14} />}
        </button>
      </div>
      <p>{text}</p>
    </article>
  );
}
