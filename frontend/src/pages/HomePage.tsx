/**
 * @fileoverview 总览仪表盘页面（首页）
 *
 * 职责：
 * - 展示全国门店分布总览地图（瑞幸蓝点 + 星巴克绿点）
 * - 呈现 4 个核心 KPI 卡片：瑞幸门店数、星巴克门店数、覆盖城市数、年均增长率
 * - 支持地图点击查询最近门店详情（含 POI 统计）
 *
 * 数据来源：
 * - useCities: 城市列表及全国门店总数
 * - useTimeline: 时间轴数据（计算年均增长率）
 * - useHeatmap x2: 瑞幸 + 星巴克热力图坐标数据（渲染为散点图图层）
 */

import React, { useMemo, useState, useCallback } from 'react';
import { Row, Col, Card, Statistic, Spin, Alert, Descriptions, Tag, Button } from 'antd';
import { ShopOutlined, EnvironmentOutlined, RiseOutlined, GlobalOutlined, CloseOutlined } from '@ant-design/icons';
import axios from 'axios';
import { useCities } from '../hooks/useCities';
import { useTimeline, useHeatmap } from '../hooks/useStoreData';
import MapView from '../components/MapView';

/** 品牌颜色映射：瑞幸蓝 #1677ff，星巴克绿 #52c41a */
const BRAND_COLORS: Record<string, string> = { luckin: '#1677ff', starbucks: '#52c41a' };

/**
 * 总览仪表盘页面
 *
 * 首页展示全国门店分布的宏观视图，包含 KPI 卡片和交互式地图。
 * 地图点击可查询点击位置附近的最近门店详情。
 *
 * 性能优化：
 * - 首次加载全屏 Spin，后续数据刷新仅内部更新，不卸载地图组件（避免视图重置）
 * - 图层配置由 useMemo 缓存，防止 deck.gl 重复创建 WebGL 资源
 */
const HomePage: React.FC = () => {
  // ─── 数据源 ───
  const { totalLuckin, totalStarbucks, citiesCovered, loading: citiesLoading, error: citiesError } = useCities();
  const { avgGrowthRate, loading: tlLoading } = useTimeline('luckin');
  const { heatmap: luckinHeatmap, loading: luckinHmLoading } = useHeatmap('luckin');
  const { heatmap: sbHeatmap, loading: sbHmLoading } = useHeatmap('starbucks');

  /** 点击地图选中的目标门店 */
  const [selectedStore, setSelectedStore] = useState<any>(null);
  /** 门店详情加载状态 */
  const [storeLoading, setStoreLoading] = useState(false);

  /** 聚合加载状态：任一数据源未就绪即为 loading */
  const loading = citiesLoading || tlLoading || luckinHmLoading || sbHmLoading;

  /**
   * 构建地图图层配置
   * useMemo 缓存：仅在热力图数据变化时重建，防止无谓的 WebGL 图层销毁/重建
   *
   * 图层 1：瑞幸门店蓝色散点（半径 3000m, 像素范围 2-12px）
   * 图层 2：星巴克门店绿色散点（半径 2000m, 像素范围 2-10px）
   * 星巴克点较小是为了让瑞幸点在视觉上更突出
   */
  const mapLayers = useMemo(() => {
    const layers: any[] = [];
    // 瑞幸图层：蓝色大点
    if (luckinHeatmap?.geojson?.coordinates) {
      layers.push({
        type: 'scatterplot' as const,
        data: luckinHeatmap.geojson.coordinates,
        getPosition: (d: any) => d as [number, number],
        getRadius: 3000,
        getFillColor: [22, 119, 255, 160] as [number, number, number, number],
        radiusMinPixels: 2,
        radiusMaxPixels: 12,
      });
    }
    // 星巴克图层：绿色小点
    if (sbHeatmap?.geojson?.coordinates) {
      layers.push({
        type: 'scatterplot' as const,
        data: sbHeatmap.geojson.coordinates,
        getPosition: (d: any) => d as [number, number],
        getRadius: 2000,
        getFillColor: [82, 196, 26, 160] as [number, number, number, number],
        radiusMinPixels: 2,
        radiusMaxPixels: 10,
      });
    }
    return layers;
  }, [luckinHeatmap, sbHeatmap]);

  /**
   * 地图点击事件处理
   * 根据点击坐标查询最近的线下门店详情
   * useCallback 缓存：无依赖，引用稳定
   */
  const handleMapClick = useCallback(async ({ coordinate }: { coordinate: [number, number] }) => {
    setStoreLoading(true);
    try {
      const res = await axios.get('/api/stores/nearest', {
        params: { lng: coordinate[0], lat: coordinate[1] },
      });
      setSelectedStore(res.data);
    } catch {
      setSelectedStore(null);
    } finally {
      setStoreLoading(false);
    }
  }, []);

  // 首次加载状态：数据完全为空时显示全屏 Spin
  // 后续加载（如切换参数）不卸载已渲染内容，仅右上角小 loading 图标提示
  if (!luckinHeatmap && !sbHeatmap && loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '80vh' }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div style={{ padding: 24 }}>
      {/* 数据加载错误提示 */}
      {citiesError && (
        <Alert message={`数据加载异常: ${citiesError}`} type="warning" showIcon style={{ marginBottom: 16 }} closable />
      )}

      {/* 第一行：4 个核心 KPI 卡片 */}
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={12} md={6}>
          <Card>
            <Statistic
              title="瑞幸全国门店"
              value={totalLuckin}
              prefix={<ShopOutlined />}
              valueStyle={{ color: '#1677ff' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={12} md={6}>
          <Card>
            <Statistic
              title="星巴克全国门店"
              value={totalStarbucks}
              prefix={<EnvironmentOutlined />}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={12} md={6}>
          <Card>
            <Statistic
              title="覆盖城市"
              value={citiesCovered}
              prefix={<GlobalOutlined />}
              valueStyle={{ color: '#722ed1' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={12} md={6}>
          <Card>
            <Statistic
              title="年均增长率"
              value={avgGrowthRate}
              prefix={<RiseOutlined />}
              suffix="%"
              valueStyle={{ color: '#fa8c16' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 第二行：全国门店分布地图 */}
      <Row style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title="全国门店分布总览" styles={{ body: { padding: 0 } }}>
            {/* 图例说明：蓝点=瑞幸，绿点=星巴克 */}
            <div style={{ display: 'flex', gap: 16, padding: '8px 16px 0', fontSize: 12, color: '#666' }}>
              <span>🔵 瑞幸 (8,526)</span>
              <span>🟢 星巴克 (4,349)</span>
            </div>
            <div style={{ height: 580 }}>
              <MapView
                viewState={{ latitude: 35, longitude: 105, zoom: 4 }}
                layers={mapLayers}
                onClick={handleMapClick}
              />
            </div>
          </Card>
        </Col>
      </Row>

      {/* 门店详情加载中 */}
      {storeLoading && (
        <Row style={{ marginTop: 16 }}>
          <Col span={24}>
            <Card><div style={{ textAlign: 'center', padding: 24 }}><Spin /></div></Card>
          </Col>
        </Row>
      )}

      {/* 门店详情卡片：展示地图点击位置最近的门店完整信息 */}
      {selectedStore && !storeLoading && (
        <Row style={{ marginTop: 16 }}>
          <Col span={24}>
            <Card
              title="📍 门店详情"
              extra={
                <Button type="text" icon={<CloseOutlined />} onClick={() => setSelectedStore(null)} />
              }
            >
              {/* 标签行：品牌 + 营业状态 + 门店类型 + 距离 */}
              <div style={{ marginBottom: 16 }}>
                <Tag color={BRAND_COLORS[selectedStore.brand]}>
                  {selectedStore.brand === 'luckin' ? '瑞幸咖啡' : '星巴克'}
                </Tag>
                <Tag color={selectedStore.status === '营业中' ? 'success' : 'default'}>{selectedStore.status}</Tag>
                {selectedStore.store_type && <Tag>{selectedStore.store_type}</Tag>}
                {selectedStore.distance_m != null && (
                  <Tag color="blue">距离点击位置 {selectedStore.distance_m}m</Tag>
                )}
              </div>

              {/* 门店名称 */}
              <h3 style={{ margin: '0 0 12px' }}>{selectedStore.name}</h3>

              {/* 基础信息表格 */}
              <Descriptions column={{ xs: 1, sm: 2, md: 3 }} size="small" bordered>
                <Descriptions.Item label="城市">{selectedStore.city}</Descriptions.Item>
                <Descriptions.Item label="区县">{selectedStore.district || '-'}</Descriptions.Item>
                <Descriptions.Item label="开业日期">{selectedStore.open_date || '-'}</Descriptions.Item>
                <Descriptions.Item label="地址" span={3}>{selectedStore.address}</Descriptions.Item>
              </Descriptions>

              {/* POI 统计：按缓冲半径分组展示各类 POI 数量 */}
              {selectedStore.poi_stats && Object.keys(selectedStore.poi_stats).length > 0 && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ fontWeight: 600, marginBottom: 8 }}>周边 POI 统计：</div>
                  {/* 遍历 200m / 500m / 1000m 三个缓冲半径 */}
                  {(['200', '500', '1000'] as const).map((radius) => {
                    const stats = selectedStore.poi_stats[radius];
                    if (!stats) return null;
                    return (
                      <div key={radius} style={{ marginBottom: 8 }}>
                        <Tag color="blue">{radius}m 缓冲区</Tag>
                        {/* 每个缓冲半径展示前 8 个 POI 类别 */}
                        {Object.entries(stats).slice(0, 8).map(([cat, count]) => (
                          <Tag key={cat}>{cat}: {count as number}</Tag>
                        ))}
                      </div>
                    );
                  })}
                </div>
              )}
            </Card>
          </Col>
        </Row>
      )}
    </div>
  );
};

export default HomePage;
