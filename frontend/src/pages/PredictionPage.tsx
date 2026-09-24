/**
 * @fileoverview 选址预测页面
 *
 * 职责：
 * - 提供交互式选址评分工具：用户在地图上点击或手动输入经纬度
 * - 调用后端 XGBoost 模型进行选址适宜度评分
 * - 展示评分结果、特征重要性分析（正面/负面影响因素）
 *
 * 交互设计：
 * - 地图点击自动填充经纬度并进行逆地理编码获取城市名
 * - 手动输入经纬度和城市名后点击"开始评分"按钮
 * - 评分结果包含百分制分数和定性推荐（强烈推荐/可以考虑/不推荐）
 */

import React, { useState, useCallback, useMemo } from 'react';
import { Card, Row, Col, Button, Input, message, Spin, Descriptions, Statistic, Tag } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import axios from 'axios';
import ReactECharts from 'echarts-for-react';
import MapView from '../components/MapView';
import { usePrediction } from '../hooks/usePrediction';

/** 默认定位：上海市中心（人民广场附近） */
const DEFAULT_LNG = 121.4737;
const DEFAULT_LAT = 31.2304;
const DEFAULT_CITY = '上海';

/**
 * 选址预测页面
 *
 * 提供交互式选址评分工具，基于 XGBoost 模型对目标位置进行适宜度评估。
 * 支持地图点击选点和手动输入经纬度两种方式。
 */
const PredictionPage: React.FC = () => {
  // ─── 状态：经纬度 + 城市 + 地图标记位置 ───
  const [lng, setLng] = useState(String(DEFAULT_LNG));
  const [lat, setLat] = useState(String(DEFAULT_LAT));
  const [city, setCity] = useState(DEFAULT_CITY);
  /** 地图红色标记的当前位置 */
  const [selectedPos, setSelectedPos] = useState<[number, number]>([DEFAULT_LNG, DEFAULT_LAT]);

  const { predict, result, loading, error, reset } = usePrediction();

  /**
   * 执行选址评分
   * useCallback 缓存：依赖经纬度字符串和 predict 函数
   * 校验经纬度为有效数字后调用后端模型接口
   */
  const handlePredict = useCallback(async () => {
    const lngNum = parseFloat(lng);
    const latNum = parseFloat(lat);
    if (isNaN(lngNum) || isNaN(latNum)) {
      message.error('请输入有效的经纬度');
      return;
    }
    try {
      await predict({ lng: lngNum, lat: latNum, city });
      setSelectedPos([lngNum, latNum]);
      message.success('评分完成');
    } catch {
      // 错误已由 hook 设置到 error 状态
    }
  }, [lng, lat, city, predict]);

  /**
   * 地图点击事件处理
   * useCallback 缓存：无外部依赖，引用稳定
   *
   * 流程：
   * 1. 从点击坐标提取 lng/lat 并更新输入框
   * 2. 调用逆地理编码 API 获取城市名
   * 3. 自动更新城市输入框
   */
  const handleMapClick = useCallback(
    async (info: { coordinate: [number, number] }) => {
      const [clng, clat] = info.coordinate;
      setLng(clng.toFixed(6));
      setLat(clat.toFixed(6));
      setSelectedPos([clng, clat]);
      // 逆地理编码：将经纬度转换为城市名
      try {
        const res = await axios.get('/api/geo/reverse', { params: { lng: clng, lat: clat } });
        if (res.data?.city) setCity(res.data.city);
      } catch { /* 逆地理编码失败不影响评分流程 */ }
    },
    [],
  );

  /**
   * 地图标记图层
   * useMemo 缓存：仅在 selectedPos 变化时重建图层对象引用
   *
   * 配置说明：
   * - 红色大圆点标记用户选择的评分位置
   * - radiusMinPixels: 8 / radiusMaxPixels: 20 确保在不同缩放级别下可见
   */
  const markerLayer = useMemo(
    () => ({
      type: 'scatterplot' as const,
      data: [{ position: selectedPos }],
      getPosition: (d: { position: [number, number] }) => d.position,
      getRadius: 100,
      getFillColor: [255, 77, 79, 200] as [number, number, number, number],
      radiusMinPixels: 8,
      radiusMaxPixels: 20,
    }),
    [selectedPos],
  );

  /**
   * ECharts 特征重要性水平柱状图
   * useMemo 缓存：仅在 result 变化时重建
   *
   * 配置说明：
   * - 展示 top_positive 特征的贡献度
   * - 水平柱状图（yAxis 为 category）更适合展示特征名称
   * - 颜色：正值绿色(#52c41a)表示正面贡献，负值红色(#ff4d4f)表示反面影响
   * - reverse() 使数据按重要性升序排列（ECharts 水平柱状图默认从下到上）
   */
  const featureChartOption = useMemo(() => {
    if (!result?.top_positive?.length) return null;
    const items = [...result.top_positive].reverse();
    return {
      tooltip: { trigger: 'axis' as const },
      xAxis: { type: 'value' as const, name: '重要性' },
      yAxis: {
        type: 'category' as const,
        data: items.map((f) => f.feature),
      },
      series: [
        {
          type: 'bar',
          data: items.map((f) => ({
            value: f.importance,
            itemStyle: { color: f.value > 0 ? '#52c41a' : '#ff4d4f' },
          })),
        },
      ],
      grid: { left: 130, right: 30 },
    };
  }, [result]);

  /**
   * 根据评分返回对应的颜色值
   * >= 0.7 绿色（推荐），>= 0.4 橙色（考虑），否则红色（不推荐）
   */
  const getScoreColor = (score: number) => {
    if (score >= 0.7) return '#52c41a';
    if (score >= 0.4) return '#fa8c16';
    return '#ff4d4f';
  };

  return (
    <div style={{ padding: 24 }}>
      <Row gutter={[16, 16]}>
        {/* 左侧面板：评分工具 + 结果展示 */}
        <Col xs={24} md={8}>
          {/* 评分工具卡片 */}
          <Card title="📍 选址评分工具" size="small">
            {/* 经度输入 */}
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: '#666' }}>经度 (lng)</label>
              <Input
                value={lng}
                onChange={(e) => {
                  setLng(e.target.value);
                  const n = parseFloat(e.target.value);
                  // 输入合法数字时同步更新地图标记位置
                  if (!isNaN(n)) setSelectedPos([n, parseFloat(lat) || DEFAULT_LAT]);
                }}
                style={{ marginTop: 4 }}
              />
            </div>
            {/* 纬度输入 */}
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: '#666' }}>纬度 (lat)</label>
              <Input
                value={lat}
                onChange={(e) => {
                  setLat(e.target.value);
                  const n = parseFloat(e.target.value);
                  if (!isNaN(n)) setSelectedPos([parseFloat(lng) || DEFAULT_LNG, n]);
                }}
                style={{ marginTop: 4 }}
              />
            </div>
            {/* 城市输入 */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12, color: '#666' }}>城市</label>
              <Input value={city} onChange={(e) => setCity(e.target.value)} style={{ marginTop: 4 }} />
            </div>
            {/* 评分按钮 */}
            <Button
              type="primary"
              icon={<SearchOutlined />}
              onClick={handlePredict}
              loading={loading}
              block
            >
              开始评分
            </Button>
            {error && <div style={{ marginTop: 8, color: '#ff4d4f', fontSize: 12 }}>{error}</div>}
            <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
              提示：可以在地图上直接点击选点，或手动输入经纬度
            </div>
          </Card>

          {/* 评分结果卡片（仅在有结果时渲染） */}
          {result && (
            <Card title="评分结果" size="small" style={{ marginTop: 16 }}>
              <div style={{ textAlign: 'center', marginBottom: 16 }}>
                {/* 百分制分数 */}
                <Statistic
                  title="选址适宜度"
                  value={(result.score * 100).toFixed(1)}
                  suffix="%"
                  valueStyle={{ color: getScoreColor(result.score), fontSize: 36 }}
                />
                {/* 定性推荐标签 */}
                <Tag
                  color={
                    result.score >= 0.7 ? 'success' : result.score >= 0.4 ? 'warning' : 'error'
                  }
                >
                  {result.score >= 0.7 ? '强烈推荐' : result.score >= 0.4 ? '可以考虑' : '不推荐'}
                </Tag>
              </div>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="模型状态">
                  {result.model_available ? '✅ XGBoost' : '⚠️ 简化评分'}
                </Descriptions.Item>
                <Descriptions.Item label="城市层级">
                  {result.features?.city_tier ? `T${result.features.city_tier}` : '-'}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          )}
        </Col>

        {/* 右侧面板：地图 + 特征重要性图 */}
        <Col xs={24} md={16}>
          {/* 交互式地图 */}
          <Card styles={{ body: { padding: 0, height: '100%' } }} style={{ height: 500, marginBottom: 16 }}>
            <MapView
              viewState={{
                latitude: parseFloat(lat) || DEFAULT_LAT,
                longitude: parseFloat(lng) || DEFAULT_LNG,
                zoom: 15,
              }}
              onClick={handleMapClick}
              layers={[markerLayer]}
            />
          </Card>

          {/* 特征重要性图表（仅在有结果时渲染） */}
          {featureChartOption && (
            <Card title="特征重要性 (Top 5)" size="small">
              <ReactECharts option={featureChartOption} style={{ height: 300 }} />
            </Card>
          )}
        </Col>
      </Row>

      {/* 模型说明：帮助用户理解评分机制 */}
      <Row style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title="关于选址模型">
            <p>
              <strong>模型类型</strong>：XGBoost 二分类器，基于瑞幸现有门店位置作为正样本、同城市随机点作为负样本训练。
            </p>
            <p>
              <strong>特征体系</strong>：以500m为半径提取办公、交通、商业、居住、教育、餐饮等7大类POI密度，覆盖200m/500m/1000m三个尺度。
            </p>
            <p>
              <strong>使用建议</strong>：评分 &gt; 70% 的区域具有与现有瑞幸门店相似的POI环境，适合作为新店候选位置进行实地考察。
            </p>
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default PredictionPage;
