/**
 * @fileoverview 应用根组件 —— 全局路由配置 + 布局框架
 *
 * 职责：
 * - 定义顶层页面布局（Header + Content 的经典后台管理结构）
 * - 配置 React Router v6 路由表，映射 5 个业务页面
 * - 提供全局 ErrorBoundary 错误边界，防止单个页面崩溃导致白屏
 * - 渲染顶部导航菜单，支持 NavLink 高亮当前路由
 */

import React, { Component } from 'react';
import { Routes, Route, NavLink } from 'react-router-dom';
import { Layout, Menu, Card, Button } from 'antd';
import {
  HomeOutlined,
  DotChartOutlined,
  AimOutlined,
  SwapOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import HomePage from './pages/HomePage';
import EvolutionPage from './pages/EvolutionPage';
import PoiAnalysisPage from './pages/PoiAnalysisPage';
import ComparisonPage from './pages/ComparisonPage';
import PredictionPage from './pages/PredictionPage';

/** 解构 Layout 组件：Header 为顶部导航栏、Content 为主体内容区 */
const { Header, Content } = Layout;

/**
 * 错误边界组件
 *
 * 捕获子组件树中未处理的 JavaScript 错误，展示友好的错误提示而非白屏。
 * 用户可点击"重试"按钮清除错误状态，触发子组件重新渲染。
 *
 * @remarks 使用 Class Component 实现，因为 React 目前不支持在函数组件中使用 componentDidCatch
 */
class ErrorBoundary extends Component<
  { children: React.ReactNode },
  { hasError: boolean; error: Error | null }
> {
  constructor(props: any) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  /** 静态方法：当子组件抛出错误时由 React 调用，返回新的 state 触发重新渲染 */
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <Card
          title="⚠️ 组件渲染异常"
          style={{ margin: 24 }}
          extra={
            <Button
              type="primary"
              size="small"
              onClick={() => this.setState({ hasError: false, error: null })}
            >
              重试
            </Button>
          }
        >
          <p style={{ color: '#ff4d4f' }}>{this.state.error?.message}</p>
          <p style={{ color: '#999', fontSize: 12 }}>
            请检查浏览器控制台 (F12) 查看完整错误信息
          </p>
        </Card>
      );
    }
    return this.props.children;
  }
}

/**
 * 导航菜单项配置
 * - key：对应路由路径，用于菜单选中状态匹配
 * - icon：Ant Design 图标组件
 * - label：中文菜单名称
 */
const navItems = [
  { key: '/', icon: <HomeOutlined />, label: '总览' },
  { key: '/evolution', icon: <DotChartOutlined />, label: '时空演变' },
  { key: '/poi-analysis', icon: <AimOutlined />, label: 'POI分析' },
  { key: '/comparison', icon: <SwapOutlined />, label: '竞品对比' },
  { key: '/prediction', icon: <ThunderboltOutlined />, label: '选址预测' },
];

/**
 * 应用根组件
 *
 * 渲染全局布局：顶部导航栏（固定深色 Header）+ 下方内容区（Routes）。
 * 所有页面切换通过 React Router 的 NavLink 实现，无需整页刷新。
 *
 * @returns 完整的应用 UI 树
 */
const App: React.FC = () => {
  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* 顶部导航栏：品牌 Logo + 水平菜单 */}
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          background: '#001529',
          padding: '0 24px',
        }}
      >
        {/* 品牌标题 */}
        <div
          style={{
            color: '#fff',
            fontSize: 18,
            fontWeight: 700,
            marginRight: 40,
            whiteSpace: 'nowrap',
          }}
        >
          🍵 瑞幸空间分析
        </div>
        {/* 水平导航菜单：每个菜单项包裹 NavLink 实现路由跳转 */}
        <Menu
          theme="dark"
          mode="horizontal"
          defaultSelectedKeys={['/']}
          style={{ flex: 1, minWidth: 0 }}
          items={navItems.map((item) => ({
            key: item.key,
            icon: item.icon,
            label: <NavLink to={item.key}>{item.label}</NavLink>,
          }))}
        />
      </Header>
      {/* 主体内容区：由 ErrorBoundary 包裹，捕获页面级渲染错误 */}
      <Content style={{ padding: 0, background: '#f0f2f5' }}>
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/evolution" element={<EvolutionPage />} />
            <Route path="/poi-analysis" element={<PoiAnalysisPage />} />
            <Route path="/comparison" element={<ComparisonPage />} />
            <Route path="/prediction" element={<PredictionPage />} />
          </Routes>
        </ErrorBoundary>
      </Content>
    </Layout>
  );
};

export default App;
