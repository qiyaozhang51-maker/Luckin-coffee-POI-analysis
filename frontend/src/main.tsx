/**
 * @fileoverview 应用入口文件
 *
 * 职责：挂载 React 根组件到 DOM，配置全局 Provider（路由、UI 国际化、全局样式）。
 * 该文件是整个前端应用的启动点，不做任何业务逻辑处理。
 *
 * 技术栈：
 * - React 18 createRoot API（并发模式）
 * - React Router v6（BrowserRouter 客户端路由）
 * - Ant Design ConfigProvider（中文语言包 + 全局主题注入）
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import './styles/global.css';

// 创建 React 18 根节点并挂载应用
// React.StrictMode：开发环境下进行额外检查（如重复渲染检测副作用）
// ConfigProvider：注入 Ant Design 中文语言包，确保所有组件文案显示为中文
// BrowserRouter：基于 HTML5 History API 的客户端路由，支持 URL 路径导航
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
