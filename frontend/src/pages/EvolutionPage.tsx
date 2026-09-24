/**
 * @fileoverview 时空演变页面
 *
 * 职责：
 * - 展示瑞幸门店随时间的空间扩张和数量增长趋势
 * - 支持按年份和城市筛选，地图与图表联动
 * - 核心可视化：热力图（空间分布）+ 面积图（增长趋势）+ 年份滑块（时间轴）
 *
 * 交互设计：
 * - 年份滑块控制地图和统计卡片的显示年份
 * - 城市选择器筛选特定城市的门店数据
 * - 切换城市时 MapView 通过 key 属性强制重新挂载，实现视角和图层完全重置
 */

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { Card, Slider, Spin, Row, Col, Statistic, Select, Alert } from 'antd';
import ReactECharts from 'echarts-for-react';
import MapView from '../components/MapView';
import { useCities } from '../hooks/useCities';
import { useTimeline, useHeatmap } from '../hooks/useStoreData';

/**
 * 时空演变页面
 *
 * 核心功能：
 * - 年份滑块驱动时间轴：选择年份后地图和统计卡片同步更新
 * - 城市筛选：切换城市后地图视角自动定位到该城市上空
 * - 增长趋势图：双轴图表（柱状=月度新增，折线=累计门店）
 *
 * 性能考虑：
 * - 首次加载全屏 Spin，后续年份切换仅更新数据不卸载地图（保留用户视角）
 * - areaChartOption 通过 useMemo 缓存，仅在 timeline 变化时重建 ECharts 配置
 * - heatmapLayer 通过 useMemo 缓存，仅在 heatmap 数据变化时重建图层
 */
const EvolutionPage: React.FC = () => {
  // ─── 状态 ───
  /** 当前选中的城市筛选（空字符串 = 全国） */
  const [city, setCity] = useState<string>('');
  /** 当前选中的年份 */
  const [year, setYear] = useState(2024);

  // ─── 数据 Hooks ───
  const { cityOptions, loading: citiesLoading, error: citiesError } = useCities();
  const { timeline, loading: tlLoading } = useTimeline('luckin', city || undefined);
  const { heatmap, loading: hmLoading } = useHeatmap('luckin', year, city || undefined);

  /**
   * 判断是否为首次加载
   * 条件：热力图和数据中任一非空 + 三个数据源中任一仍在加载
   * 首次加载显示全屏 Spin，后续刷新时保留页面结构避免地图重置
   */
  const isFirstLoad = (!heatmap || timeline.length === 0) && (citiesLoading || tlLoading || hmLoading);

  /**
   * 计算时间轴的最小年份
   * useMemo 缓存：仅在 timeline 变化时重新计算
   * 从第一个数据点的 month 字段解析年份，回退为 2017
   */
  const minYear = useMemo(() => {
    if (timeline.length === 0) return 2017;
    return parseInt(timeline[0].month.split('-')[0]);
  }, [timeline]);
  const maxYear = 2026;

  /**
   * 副作用：首次加载数据后自动设定默认年份
   * 将 year 设置为数据中最后一个月的年份，确保地图展示最新数据
   */
  useEffect(() => {
    if (timeline.length > 0) {
      const lastMonth = timeline[timeline.length - 1];
      const lastYear = parseInt(lastMonth.month.split('-')[0]);
      setYear((prev) => (prev === 2024 ? lastYear : prev));
    }
  }, [timeline]);

  /**
   * 计算当前年份的统计指标
   * useMemo 缓存：依赖 timeline 和 year
   * - stores: 该年最后一个月所属的累计门店数
   * - cities: 粗略估算覆盖城市 = 年度新增 / 3（假设每个城市年均开 3 家）
   */
  const currentStats = useMemo(() => {
    const yearData = timeline.filter((t) => t.month.startsWith(String(year)));
    const totalNew = yearData.reduce((s, t) => s + t.new_stores, 0);
    const lastCum = yearData[yearData.length - 1]?.cumulative_stores || 0;
    return { stores: lastCum, cities: Math.max(1, Math.round(totalNew / 3)) };
  }, [timeline, year]);

  /** 年份滑块变化处理 */
  const handleYearChange = useCallback((val: number | null) => {
    if (val !== null) setYear(val);
  }, []);

  /**
   * 滑块年份标记
   * useMemo 缓存：仅在年份范围变化时重建
   */
  const sliderMarks = useMemo(() => {
    const marks: Record<number, string> = {};
    for (let y = minYear; y <= maxYear; y++) {
      marks[y] = String(y);
    }
    return marks;
  }, [minYear, maxYear]);

  /**
   * ECharts 面积图配置
   * useMemo 缓存：仅在 timeline 变化时重建
   *
   * 配置说明：
   * - xAxis: 时间轴，每隔 3 个月取一个标签避免过密
   * - yAxis 双轴: 左轴=月度新增（柱状图），右轴=累计门店（折线）
   * - 柱状图蓝色(#1677ff)表示新增，折线绿色(#52c41a)表示累计
   * - grid 内边距确保标签完整显示
   */
  const areaChartOption = useMemo(() => ({
    tooltip: { trigger: 'axis' as const },
    xAxis: {
      type: 'category' as const,
      data: timeline.filter((_, i) => i % 3 === 0).map((t) => t.month),
    },
    yAxis: [
      { type: 'value' as const, name: '新增门店' },
      { type: 'value' as const, name: '累计门店' },
    ],
    series: [
      {
        name: '月度新增',
        type: 'bar',
        data: timeline.filter((_, i) => i % 3 === 0).map((t) => t.new_stores),
        itemStyle: { color: '#1677ff' },
      },
      {
        name: '累计门店',
        type: 'line',
        yAxisIndex: 1,
        data: timeline.filter((_, i) => i % 3 === 0).map((t) => t.cumulative_stores),
        smooth: true,
        itemStyle: { color: '#52c41a' },
      },
    ],
    grid: { left: 60, right: 60, bottom: 40, top: 20 },
  }), [timeline]);

  /**
   * 热力图图层配置
   * useMemo 缓存：仅在 heatmap 数据变化时重建
   *
   * 配置说明：
   * - type: 'heatmap' 使用 deck.gl 热力聚合图层
   * - radiusPixels: 40 控制热力影响半径
   * - threshold: 0.05 过滤低密度噪点
   */
  const heatmapLayer = useMemo(() => {
    if (!heatmap?.geojson?.coordinates) return [];
    return [{
      type: 'heatmap' as const,
      data: heatmap.geojson.coordinates,
      getPosition: (d: any) => d as [number, number],
      radiusPixels: 40,
      intensity: 1,
      threshold: 0.05,
    }];
  }, [heatmap]);

  // ─── 渲染 ───
  /** 首次加载：全屏 Spin */
  if (isFirstLoad) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '80vh' }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div style={{ padding: 24 }}>
      {/* 数据错误提示 */}
      {citiesError && (
        <Alert message={`数据异常: ${citiesError}`} type="warning" showIcon style={{ marginBottom: 16 }} closable />
      )}

      {/* 非首次加载时的刷新指示器：右上角小 loading 图标，不卸载页面内容 */}
      {!isFirstLoad && (tlLoading || hmLoading) && (
        <div style={{ position: 'fixed', top: 80, right: 24, zIndex: 1000 }}>
          <Spin size="small" />
        </div>
      )}

      {/* 第一行：统计卡片 + 城市选择器 */}
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={12} md={6}>
          <Card>
            <Statistic title={`${year}年累计门店`} value={currentStats.stores} valueStyle={{ color: '#1677ff' }} />
          </Card>
        </Col>
        <Col xs={12} sm={12} md={6}>
          <Card>
            <Statistic title="覆盖城市数" value={currentStats.cities} valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col xs={24} sm={24} md={12}>
          <Card size="small">
            <Select
              style={{ width: '100%' }}
              placeholder="选择城市（全部）"
              allowClear
              value={city || undefined}
              onChange={(val) => setCity(val || '')}
              options={cityOptions}
              showSearch
              filterOption={(input, option) =>
                (option?.label as string).toLowerCase().includes(input.toLowerCase())
              }
            />
          </Card>
        </Col>
      </Row>

      {/* 第二行：门店分布地图 */}
      <Row style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title={`${year}年门店分布`} styles={{ body: { padding: 0 } }}>
            <div style={{ height: 500 }}>
              <MapView
                /* key 属性：城市变化时强制 React 卸载旧 MapView 并挂载新实例，
                   确保 deck.gl 完全重建 WebGL 上下文并重新定位地图视角 */
                key={city || '__all__'}
                viewState={{
                  /** 有城市筛选时定位到东部城市中心 (118°E, 32°N) + 10级缩放，
                      无筛选时定位到全国中心 (105°E, 35°N) + 4级缩放 */
                  latitude: city ? 32 : 35,
                  longitude: city ? 118 : 105,
                  zoom: city ? 10 : 4,
                }}
                layers={heatmapLayer}
              />
            </div>
          </Card>
        </Col>
      </Row>

      {/* 第三行：年份滑块 */}
      <Row style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card>
            <div style={{ padding: '0 24px' }}>
              <Slider
                min={minYear}
                max={maxYear}
                value={year}
                onChange={handleYearChange}
                marks={sliderMarks}
                step={1}
                tooltip={{ formatter: (val) => `${val}年` }}
              />
            </div>
          </Card>
        </Col>
      </Row>

      {/* 第四行：增长趋势图表 */}
      <Row style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title="门店增长趋势">
            <ReactECharts option={areaChartOption} style={{ height: 350 }} />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default EvolutionPage;
