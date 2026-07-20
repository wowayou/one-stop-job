import { Component, ErrorInfo, ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

// 顶层渲染兜底：任何子树抛出的渲染错误都会被这里捕获，
// 避免整页白屏，并给出可恢复的重新加载入口。
class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 仅本地打印，供开发者在控制台定位；不外发。
    console.error("界面渲染出错：", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="crash-screen" role="alert">
        <div className="crash-card">
          <h1>界面遇到问题</h1>
          <p>页面在渲染时出错了，你的数据仍然安全地保存在本机。可以先重新加载页面恢复使用。</p>
          <pre className="crash-detail">{this.state.error.message}</pre>
          <div className="crash-actions">
            <button type="button" className="primary-action" onClick={() => window.location.reload()}>
              重新加载
            </button>
            <button type="button" className="small-action" onClick={() => this.setState({ error: null })}>
              尝试继续
            </button>
          </div>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
