// React 入口：把应用根组件挂载到 #root。
// /resign/<token> 是员工侧离职填报表单的公开页，不经登录门直接渲染。

import { createRoot } from "react-dom/client";
import { Root } from "./views/LoginView";
import { ResignFormView, resignTokenFromPath } from "./views/ResignFormView";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Missing #root container");
}

const resignToken = resignTokenFromPath(window.location.pathname);
createRoot(container).render(
  resignToken ? <ResignFormView token={resignToken} /> : <Root />
);
