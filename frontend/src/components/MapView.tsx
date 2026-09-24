/**
 * @fileoverview deck.gl 地图可视化组件 —— 项目的核心地图渲染模块
 *
 * 职责：
 * - 基于 deck.gl + MapLibre GL 渲染可交互的地理可视化图层
 * - 支持散点图、热力图、柱状图、GeoJSON 四种图层类型
 * - 提供悬浮弹窗（Tooltip）展示门店详细信息
 * - 使用受控 viewState 防止数据更新时地图视角意外重置
 *
 * 底图：高德矢量瓦片（中文标注）
 * 图层工厂：createDeckLayer 根据 LayerConfig 动态创建对应的 deck.gl Layer 实例
 */

import React, { useMemo, useState, useCallback } from 'react';
import DeckGL from '@deck.gl/react';
import { ScatterplotLayer, ColumnLayer, GeoJsonLayer } from '@deck.gl/layers';
import { HeatmapLayer } from '@deck.gl/aggregation-layers';
import type { Layer } from '@deck.gl/core';
import { Map } from 'react-map-gl';
import maplibregl from 'maplibre-gl';
import type { MapViewState } from '@deck.gl/core';
import StorePopup from './StorePopup';

/**
 * 中文底图样式配置（高德矢量瓦片）
 *
 * 采用 MapLibre GL 样式规范 v8，将高德地图的栅格瓦片作为 raster 图层渲染。
 *
 * @field version - MapLibre 样式规范版本号
 * @field glyphs - 字体字形服务 URL（用于文本标注渲染）
 * @field sources.amap-vec - 高德矢量瓦片数据源，4 个子域名实现浏览器并发加载
 *   @field type - 栅格瓦片类型（raster）
 *   @field tiles - 瓦片 URL 模板数组（使用 4 个子域名分散请求）
 *   @field tileSize - 单个瓦片像素尺寸（256px）
 *   @field minzoom/maxzoom - 缩放级别范围（0-18）
 *   @field attribution - 地图版权声明
 * @field layers - 样式图层数组，将 amap-vec 数据源渲染为栅格图层
 */
const MAP_STYLE = {
  version: 8 as const,
  glyphs: 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf',
  sources: {
    'amap-vec': {
      type: 'raster' as const,
      tiles: [
        'https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
        'https://webrd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
        'https://webrd03.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
        'https://webrd04.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
      ],
      tileSize: 256,
      minzoom: 0,
      maxzoom: 18,
      attribution: '&copy; 高德地图',
    },
  },
  layers: [
    {
      id: 'amap-tiles',
      type: 'raster' as const,
      source: 'amap-vec',
      minzoom: 0,
      maxzoom: 18,
    },
  ],
};

/**
 * 散点图图层配置接口
 * 用于在指定地理坐标上绘制圆点，表现门店位置分布
 */
export interface ScatterplotLayerConfig {
  type: 'scatterplot';
  /** 图层数据源，通常为坐标数组 */
  data: any[];
  /** 获取每个数据点的 [lng, lat] 坐标 */
  getPosition: (d: any) => [number, number];
  /** 点半径（米），支持数值或按数据动态计算 */
  getRadius?: number | ((d: any) => number);
  /** 填充颜色 RGBA，支持固定色或按数据动态着色 */
  getFillColor?: [number, number, number, number] | ((d: any) => [number, number, number, number]);
  /** 最小像素半径，防止缩放时点过小不可见 */
  radiusMinPixels?: number;
  /** 最大像素半径，防止缩放时点过大覆盖地图 */
  radiusMaxPixels?: number;
  /** 图层透明度 (0-1) */
  opacity?: number;
}

/**
 * 热力图图层配置接口
 * 用于展示点的密度分布，通过颜色渐变表现聚集程度
 */
export interface HeatmapLayerConfig {
  type: 'heatmap';
  /** 图层数据源 */
  data: any[];
  /** 获取每个数据点的 [lng, lat] 坐标 */
  getPosition: (d: any) => [number, number];
  /** 热力影响半径（像素），越大热力范围越广 */
  radiusPixels?: number;
  /** 热力强度系数，控制颜色饱和程度 */
  intensity?: number;
  /** 热力阈值，低于此值的点不计入热力 */
  threshold?: number;
  /** 图层透明度 (0-1) */
  opacity?: number;
}

/**
 * 柱状图（3D 立柱）图层配置接口
 * 用于在三维视角下展示数值高度，适合表现区域门店数量对比
 */
export interface ColumnLayerConfig {
  type: 'column';
  /** 图层数据源 */
  data: any[];
  /** 获取每个数据点的 [lng, lat] 坐标 */
  getPosition: (d: any) => [number, number];
  /** 柱子高度（米），支持数值或按数据动态计算 */
  getElevation?: number | ((d: any) => number);
  /** 柱子颜色 RGBA */
  getFillColor?: [number, number, number, number] | ((d: any) => [number, number, number, number]);
  /** 柱子底面半径（米） */
  radius?: number;
  /** 图层透明度 (0-1) */
  opacity?: number;
}

/**
 * GeoJSON 图层配置接口
 * 用于渲染行政区边界、缓冲区等多边形数据，支持挤出（3D 效果）
 */
export interface GeoJsonLayerConfig {
  type: 'geojson';
  /** GeoJSON FeatureCollection 数据 */
  data: any;
  /** 多边形填充颜色 RGBA */
  getFillColor?: [number, number, number, number] | ((d: any) => [number, number, number, number]);
  /** 多边形边线颜色 RGBA */
  getLineColor?: [number, number, number, number];
  /** 边线宽度（像素） */
  getLineWidth?: number;
  /** 图层透明度 (0-1) */
  opacity?: number;
  /** 是否启用 3D 挤出效果 */
  extruded?: boolean;
  /** 挤出高度（米），仅在 extruded=true 时生效 */
  getElevation?: number | ((d: any) => number);
}

/** 图层配置联合类型，覆盖所有支持的图层类型 */
export type LayerConfig =
  | ScatterplotLayerConfig
  | HeatmapLayerConfig
  | ColumnLayerConfig
  | GeoJsonLayerConfig;

/**
 * MapView 组件的 Props 类型
 */
interface MapViewProps {
  /** 初始地图视角状态：经纬度、缩放级别、俯仰角、旋转角 */
  viewState: Partial<MapViewState>;
  /** 图层配置数组，每个元素对应一个可视化图层 */
  layers?: LayerConfig[];
  /** 点击事件回调：参数包含点击坐标和被点击的数据对象 */
  onClick?: (info: { coordinate: [number, number]; object?: any }) => void;
  /**
   * 悬浮弹窗数据获取函数
   * 接收当前悬浮位置的数据对象，返回门店摘要信息或 null（不显示弹窗）
   */
  getPopupInfo?: (object: any) => {
    name: string;
    brand: string;
    address: string;
    city: string;
    district: string;
    open_date: string;
    store_type: string;
    status: string;
  } | null;
  /** 地图视角变化回调：当用户拖拽/缩放地图时触发 */
  onViewStateChange?: (viewState: MapViewState) => void;
}

/**
 * 图层工厂函数
 *
 * 根据传入的 LayerConfig 配置对象动态创建对应的 deck.gl Layer 实例。
 * 每种图层类型使用默认值作为回退，确保未配置的参数也有合理展示效果。
 *
 * @param config - 图层配置对象（散点图/热力图/柱状图/GeoJSON）
 * @param idx - 图层索引，用于生成唯一的图层 ID，防止 GPU 状态混乱
 * @returns deck.gl Layer 实例，支持鼠标交互（pickable）
 */
function createDeckLayer(config: LayerConfig, idx: number): Layer {
  /** 图层基础配置：唯一 ID（防 GPU 状态冲突）+ 启用拾取（支持 hover/click） */
  const base = { id: `layer-${idx}-${config.type}`, pickable: true };

  switch (config.type) {
    case 'scatterplot':
      // 散点图层：渲染为圆形点，默认蓝色系
      return new ScatterplotLayer({
        ...base,
        data: config.data,
        getPosition: config.getPosition,
        getRadius: config.getRadius ?? 50,
        getFillColor: config.getFillColor ?? [22, 119, 255, 200],
        radiusMinPixels: config.radiusMinPixels ?? 2,
        radiusMaxPixels: config.radiusMaxPixels ?? 30,
        opacity: config.opacity ?? 0.8,
      });
    case 'heatmap':
      // 热力图层：通过颜色渐变展现密度分布，默认暖色系
      return new HeatmapLayer({
        ...base,
        data: config.data,
        getPosition: config.getPosition,
        radiusPixels: config.radiusPixels ?? 40,
        intensity: config.intensity ?? 1,
        threshold: config.threshold ?? 0.05,
        opacity: config.opacity ?? 0.7,
      });
    case 'column':
      // 柱状图层：3D 立柱，适合表现区域数量对比
      return new ColumnLayer({
        ...base,
        data: config.data,
        getPosition: config.getPosition,
        getElevation: config.getElevation ?? 100,
        getFillColor: config.getFillColor ?? [22, 119, 255, 200],
        radius: config.radius ?? 200,
        opacity: config.opacity ?? 0.8,
      });
    case 'geojson':
      // GeoJSON 图层：多边形渲染，支持挤出（3D 效果）
      return new GeoJsonLayer({
        ...base,
        data: config.data,
        getFillColor: config.getFillColor ?? [22, 119, 255, 140],
        getLineColor: config.getLineColor ?? [0, 0, 0, 200],
        getLineWidth: config.getLineWidth ?? 1,
        extruded: config.extruded ?? false,
        getElevation: config.getElevation ?? 0,
        opacity: config.opacity ?? 0.8,
      });
    default:
      // 未知类型回退为空散点图层，避免报错
      return new ScatterplotLayer({ ...base, data: [], getPosition: () => [0, 0] });
  }
}

/**
 * MapView 地图可视化组件
 *
 * 核心交互层：将 deck.gl 的 WebGL 渲染层、MapLibre GL 的底图瓦片层、React 状态管理
 * 融合为一个完整的地理可视化组件。
 *
 * 关键设计决策：
 * - 使用受控 viewState（React state 持有视角状态），避免父组件 layers 变化时 deck.gl
 *   内部自动重置导致地图"跳回"默认视角
 * - 悬浮弹窗采用绝对定位 overlay，而非 deck.gl 内置 tooltip，以获得更灵活的样式控制
 * - 图层数组由 useMemo 缓存，仅在 layers 引用变化时重建，避免 GPU 资源浪费
 *
 * @param props.viewState - 初始地图视角状态
 * @param props.layers - 图层配置数组
 * @param props.onClick - 地图点击回调
 * @param props.getPopupInfo - 悬浮弹窗数据获取函数
 * @param props.onViewStateChange - 视角变化回调
 */
const MapView: React.FC<MapViewProps> = ({
  viewState: initialViewStateProp,
  layers = [],
  onClick,
  getPopupInfo,
  onViewStateChange,
}) => {
  /** 当前悬浮信息：鼠标位置 + 悬浮数据对象 */
  const [hoverInfo, setHoverInfo] = useState<{ x: number; y: number; object: any } | null>(null);

  /**
   * 受控 viewState：用 React state 持有地图视角
   * 目的：防止 deck.gl 因 layers 属性变化触发内部 reset 导致视图跳回默认位置
   * 初始值从 props 获取，后续变更通过 handleViewStateChange 同步
   */
  const [viewState, setViewState] = useState<MapViewState>(() => ({
    latitude: initialViewStateProp.latitude ?? 35,
    longitude: initialViewStateProp.longitude ?? 105,
    zoom: initialViewStateProp.zoom ?? 4,
    pitch: initialViewStateProp.pitch ?? 0,
    bearing: initialViewStateProp.bearing ?? 0,
  }));

  /**
   * 将 LayerConfig[] 转换为 deck.gl Layer 实例数组
   * useMemo 缓存：仅在 layers 引用变化时重建，避免每次渲染都重新创建 WebGL 图层导致性能下降
   */
  const deckLayers = useMemo(
    () => layers.map((cfg, i) => createDeckLayer(cfg, i)),
    [layers],
  );

  /**
   * 悬浮事件处理
   * useCallback 缓存：依赖 getPopupInfo，确保回调引用稳定避免 deck.gl 重复订阅
   */
  const handleHover = useCallback(
    (info: any) => {
      if (info.coordinate && info.object && getPopupInfo) {
        // 有效悬浮：记录鼠标位置和数据对象，用于渲染弹窗
        setHoverInfo({ x: info.x, y: info.y, object: info.object });
      } else {
        // 移出图层范围：清除悬浮状态
        setHoverInfo(null);
      }
    },
    [getPopupInfo],
  );

  /**
   * 点击事件处理
   * 透传 deck.gl click 事件给父组件，包含坐标和数据对象
   */
  const handleClick = useCallback(
    (info: any) => {
      if (onClick && info.coordinate) {
        onClick({ coordinate: info.coordinate, object: info.object });
      }
    },
    [onClick],
  );

  /**
   * 视角变化处理
   * 同步 deck.gl 内部 viewState 到 React state，并通知父组件
   */
  const handleViewStateChange = useCallback(
    ({ viewState: newViewState }: { viewState: MapViewState }) => {
      setViewState(newViewState);
      onViewStateChange?.(newViewState);
    },
    [onViewStateChange],
  );

  /** 计算弹窗数据：仅在 hoverInfo 存在且有 getPopupInfo 时执行 */
  const popupData = hoverInfo && getPopupInfo ? getPopupInfo(hoverInfo.object) : null;

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {/* deck.gl 渲染层：WebGL 可视化图层叠加在 MapLibre GL 底图之上 */}
      <DeckGL
        viewState={viewState}
        controller={true}
        // deck.gl 9 的 onViewStateChange 是泛型签名
        //   <T extends TransitionProps | MapViewState>(params: ViewStateChangeParameters<T>) => ...
        // 而本组件的受控 viewState 是窄类型 MapViewState，两者不兼容，tsc 会报 TS2322。
        // 运行时 deck.gl 传入的 viewState 始终是 MapViewState（TransitionProps 仅在过渡动画
        // 中作为内部中间态出现，且会被本组件立即覆盖），故此处显式收窄类型。
        onViewStateChange={handleViewStateChange as never}
        layers={deckLayers}
        onClick={handleClick}
        onHover={handleHover}
        getCursor={({ isHovering }) => (isHovering ? 'pointer' : 'default')}
      >
        {/* MapLibre GL 底图层：高德中文矢量瓦片 */}
        <Map mapLib={maplibregl as any} mapStyle={MAP_STYLE} attributionControl={false} />
      </DeckGL>

      {/* 悬浮弹窗：绝对定位跟随鼠标，pointer-events: none 防止遮挡地图交互 */}
      {popupData && hoverInfo && (
        <div
          style={{
            position: 'absolute',
            left: hoverInfo.x + 12,
            top: hoverInfo.y + 12,
            zIndex: 10,
            pointerEvents: 'none',
          }}
        >
          <div
            style={{
              background: '#fff',
              borderRadius: 8,
              boxShadow: '0 2px 12px rgba(0,0,0,0.15)',
              padding: 12,
            }}
          >
            <StorePopup store={popupData} />
          </div>
        </div>
      )}
    </div>
  );
};

export default MapView;
