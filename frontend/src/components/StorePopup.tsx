/**
 * @fileoverview 门店信息悬浮弹窗组件
 *
 * 职责：
 * - 在地图悬浮或点击交互时，展示目标门店的详细信息卡片
 * - 支持显示品牌标签、营业状态、门店类型、地址、开业日期
 * - 可选展示周边 POI 统计数据（不同缓冲半径下的各类 POI 数量）
 *
 * 使用场景：被 MapView 组件作为 tooltip 内容渲染，也用于各页面的门店详情卡片
 */

import React from 'react';
import { Descriptions, Tag } from 'antd';
import { ShopOutlined } from '@ant-design/icons';

/**
 * 门店弹窗组件的 Props 类型
 */
interface StorePopupProps {
  /** 门店核心信息 */
  store: {
    /** 门店名称 */
    name: string;
    /** 品牌标识：'luckin'（瑞幸）或 'starbucks'（星巴克） */
    brand: string;
    /** 详细地址 */
    address: string;
    /** 所在城市 */
    city: string;
    /** 所在区县 */
    district: string;
    /** 开业日期（YYYY-MM-DD 格式） */
    open_date: string;
    /** 门店类型（如快取店、悠享店等） */
    store_type: string;
    /** 营业状态（如"营业中"、"已关闭"） */
    status: string;
  };
  /** 周边 POI 统计：外层 key 为缓冲半径，内层 key 为 POI 类别，value 为数量 */
  poiStats?: Record<string, Record<string, number>>;
}

/** 品牌颜色映射：瑞幸蓝色 #1677ff，星巴克绿色 #52c41a */
const BRAND_COLORS: Record<string, string> = {
  luckin: '#1677ff',
  starbucks: '#52c41a',
};

/**
 * 门店信息弹窗组件
 *
 * 展示门店的完整信息卡片，包括品牌标签（彩色）、营业状态、地址、城市-区县、
 * 开业日期等核心字段。当提供 poiStats 时，额外展示 500m 缓冲半径下的 POI 统计。
 *
 * @param props.store - 门店信息对象
 * @param props.poiStats - 可选的周边 POI 统计数据
 */
const StorePopup: React.FC<StorePopupProps> = ({ store, poiStats }) => {
  return (
    <div style={{ minWidth: 280, maxWidth: 360 }}>
      {/* 标签行：品牌（彩色）+ 营业状态 + 门店类型 */}
      <div style={{ marginBottom: 12 }}>
        <Tag color={BRAND_COLORS[store.brand]}>
          {store.brand === 'luckin' ? '瑞幸咖啡' : '星巴克'}
        </Tag>
        <Tag color={store.status === '营业中' ? 'success' : 'default'}>{store.status}</Tag>
        {store.store_type && <Tag>{store.store_type}</Tag>}
      </div>

      {/* 门店名称 */}
      <h4 style={{ margin: '0 0 8px' }}>{store.name}</h4>
      {/* 详细地址 */}
      <p style={{ color: '#666', fontSize: 13, margin: 0 }}>{store.address}</p>
      {/* 城市·区县 + 开业日期 */}
      <p style={{ color: '#999', fontSize: 12, margin: '4px 0 12px' }}>
        {store.city} · {store.district}
        {store.open_date && ` · 开业: ${store.open_date}`}
      </p>

      {/* 周边 POI 统计（仅在有数据时渲染） */}
      {poiStats && (
        <div>
          <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>周边POI (500m):</div>
          {/* 展示前 6 个 POI 类别及其数量，避免信息过载 */}
          {Object.entries(poiStats['500'] || {}).slice(0, 6).map(([cat, count]) => (
            <span key={cat} style={{ marginRight: 12, fontSize: 12, color: '#666' }}>
              {cat}: {count}
            </span>
          ))}
        </div>
      )}
    </div>
  );
};

export default StorePopup;
