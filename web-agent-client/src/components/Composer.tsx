import {
  type KeyboardEvent,
  type RefObject,
  useLayoutEffect,
} from "react";
import { Send } from "lucide-react";
import { shouldSubmitOnEnter } from "../lib/client-state";

const MAX_COMPOSER_ROWS = 5;

type ComposerProps = {
  value: string;
  disabled: boolean;
  threadLoading: boolean;
  interrupted: boolean;
  apiUrl: string;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
};

export function Composer({
  value,
  disabled,
  threadLoading,
  interrupted,
  apiUrl,
  inputRef,
  onChange,
  onSubmit,
}: ComposerProps) {
  useLayoutEffect(() => {
    const textarea = inputRef.current;
    if (!textarea) return;

    textarea.style.height = "auto";
    textarea.style.overflowY = "hidden";
    const style = window.getComputedStyle(textarea);
    const lineHeight = Number.parseFloat(style.lineHeight) || 20;
    const verticalPadding =
      (Number.parseFloat(style.paddingTop) || 0) +
      (Number.parseFloat(style.paddingBottom) || 0);
    const maxHeight = lineHeight * MAX_COMPOSER_ROWS + verticalPadding;
    const contentHeight = textarea.scrollHeight;
    textarea.style.height = `${Math.min(contentHeight, maxHeight)}px`;
    textarea.style.overflowY = contentHeight > maxHeight ? "auto" : "hidden";
  }, [inputRef, value]);

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (shouldSubmitOnEnter(event.key, event.shiftKey, event.nativeEvent.isComposing)) {
      event.preventDefault();
      void onSubmit();
    }
  };

  return (
    <footer className="composer-area">
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          void onSubmit();
        }}
      >
        <textarea
          ref={inputRef}
          aria-label="对话输入框"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            threadLoading
              ? "正在加载对话..."
              : interrupted
                ? "输入补充信息并继续..."
                : "向 Agent 描述测试任务..."
          }
          rows={1}
          disabled={disabled}
        />
        <button
          className="send-button"
          type="submit"
          disabled={!value.trim() || disabled}
          title="发送"
        >
          <Send size={18} />
        </button>
      </form>
      <div className="composer-meta">
        <span>{interrupted ? "回复将恢复当前任务" : "Enter 发送，Shift + Enter 换行"}</span>
        <span>{apiUrl}</span>
      </div>
    </footer>
  );
}
