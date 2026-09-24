/**
 * @fileoverview POI 关联分析页面
 *
 * 职责：
 * - 展示瑞幸门店周边各类型 POI 的统计分布和富集程度（Lift 值）
 * - 提供雷达图（POI 密度辐射状对比）+ Lift 柱状图（富集程度排序）+ 详情表格
 * - 支持切换缓冲半径（200m/500m/1000m）和城市筛选
 *
 * 核心指标：
 * - Lift > 1.5：该 POI 在门店周边显著富集，是关键选址吸引因素
 * - Lift ≈ 1.0：与随机分布无显著差异
 * - Lift < 0.8：门店倾向于避开该类型 POI
 */

import React, { useState, useMemo } from 'react';
import { Card, Spin, Row, Col, Table, Select, Alert } from 'antd';
import ReactECharts from 'echarts-for-react';
import { usePoiAnalysis } from '../hooks/usePoiAnalysis';
import { useCities } from '../hooks/useCities';

/** 缓冲半径选项：200m（微观）、500m（社区）、1000m（区域） */
const RADIUS_OPTIONS = [
  { label: '200m', value: 200 },
  { label: '500m', value: 500 },
  { label: '1000m', value: 1000 },
];

/**
 * POI 关联分析页面
 *
 * 分析瑞幸门店周边各类型 POI 的密度分布和富集程度（Lift 值）。
 * 包含三个可视化组件：雷达图、Lift 柱状图、详情表格。
 */
const PoiAnalysisPage: React.FC = () => {
  /** 缓冲区半径 */
  const [radius, setRadius] = useState(500);
  /** 城市筛选 */
  const [city, setCity] = useState<string>('');

  const { correlation, liftData, loading, error } = usePoiAnalysis('luckin', radius, city || undefined);
  const { cityOptions, loading: citiesLoading } = useCities();

  /**
   * ECharts 雷达图配置
   * useMemo 缓存：仅在 correlation 变化时重建
   *
   * 配置说明：
   * - indicator: 每个 POI 类别作为一个轴，max 取最大值的 1.2 倍留出视觉空间
   * - 数据系列：单个系列表示"门店周边均值"，蓝色半透明填充
   * - 雷达图适合展现多维度指标的平衡关系
   */
  const radarOption = useMemo(() => {
    if (!correlation.length) return {};
    const maxVal = Math.max(...correlation.map((c) => c.avg_count)) * 1.2;
    return {
      tooltip: {},
      legend: { data: ['门店周边均值'] },
      radar: {
        indicator: correlation.map((c) => ({ name: c.category, max: maxVal || 1 })),
      },
      series: [
        {
          type: 'radar',
          name: '门店周边均值',
          data: [{ value: correlation.map((c) => c.avg_count), name: '门店周边均值' }],
          areaStyle: { color: 'rgba(22, 119, 255, 0.2)' },
          itemStyle: { color: '#1677ff' },
        },
      ],
    };
  }, [correlation]);

  /**
   * ECharts Lift 值柱状图配置
   * useMemo 缓存：仅在 liftData 变化时重建
   *
   * 配置说明：
   * - xAxis: POI 类别
   * - yAxis: Lift 值
   * - 颜色规则：Lift > 1.5 绿色（显著富集），> 1.0 蓝色（弱富集），其他灰色
   * - markLine: y=1 红色虚线表示基准线（随机分布水平）
   */
  const liftBarOption = useMemo(() => {
    if (!liftData.length) return {};
    return {
      tooltip: { trigger: 'axis' as const },
      xAxis: { type: 'category' as const, data: liftData.map((l) => l.category) },
      yAxis: { type: 'value' as const, name: 'Lift值' },
      series: [
        {
          type: 'bar',
          data: liftData.map((l) => ({
            value: l.lift,
            // 根据 Lift 值着色：>1.5 绿色（显著富集），>1.0 蓝色（弱富集），其他灰色
            itemStyle: {
              color: l.lift > 1.5 ? '#52c41a' : l.lift > 1.0 ? '#1677ff' : '#d9d9d9',
            },
          })),
          /** 基准线 y=1，表示与随机分布无差异 */
          markLine: {
            data: [
              { yAxis: 1, label: { formatter: '基准线' }, lineStyle: { color: '#ff4d4f', type: 'dashed' as const } },
            ],
          },
        },
      ],
    };
  }, [liftData]);

  /**
   * 详情表格列配置
   * useMemo 缓存：仅在 liftData 变化时重建
   *
   * Lift 值列：从 liftData 中查找对应 POI 类别的 Lift 值并着色显示
   */
  const columns = useMemo(
    () => [
      { title: 'POI类别', dataIndex: 'category', key: 'category' },
      {
        title: '平均数量',
        dataIndex: 'avg_count',
        key: 'avg_count',
        sorter: (a: any, b: any) => a.avg_count - b.avg_count,
      },
      { title: '标准差', dataIndex: 'stddev', key: 'stddev' },
      { title: '最大值', dataIndex: 'max_count', key: 'max_count' },
      {
        title: 'Lift值',
        dataIndex: 'lift',
        key: 'lift',
        render: (_: any, record: any) => {
          const l = liftData.find((x) => x.category === record.category);
          if (!l) return '-';
          return (
            <span style={{ color: l.lift > 1.5 ? '#52c41a' : l.lift > 1.0 ? '#1677ff' : '#999', fontWeight: 600 }}>
              {l.lift.toFixed(2)}
            </span>
          );
        },
      },
    ],
    [liftData],
  );

  // 首次加载：数据完全为空时显示全屏 Spin
  if (!correlation.length && (loading || citiesLoading)) {
    return <div style={{ textAlign: 'center', padding: 100 }}><Spin size="large" tip="加载中..." /></div>;
  }

  return (
    <div style={{ padding: 24 }}>
      {error && <Alert message={`数据异常: ${error}`} type="warning" showIcon style={{ marginBottom: 16 }} closable />}

      {/* 筛选控制栏：缓冲半径 + 城市筛选 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <label style={{ fontSize: 12, color: '#666', display: 'block', marginBottom: 4 }}>缓冲区半径</label>
          <Select
            style={{ width: '100%' }}
            value={radius}
            onChange={(val) => setRadius(val)}
            options={RADIUS_OPTIONS}
          />
        </Col>
        <Col xs={12} sm={6}>
          <label style={{ fontSize: 12, color: '#666', display: 'block', marginBottom: 4 }}>城市筛选</label>
          <Select
            style={{ width: '100%' }}
            placeholder="全部城市"
            allowClear
            value={city || undefined}
            onChange={(val) => setCity(val || '')}
            options={cityOptions}
            showSearch
            filterOption={(input, option) =>
              (option?.label as string).toLowerCase().includes(input.toLowerCase())
            }
          />
        </Col>
      </Row>

      {/* 图表区：雷达图 + Lift 柱状图 */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title={`POI雷达图 (${radius}m缓冲区)`}>
            <ReactECharts option={radarOption} style={{ height: 400 }} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="POI Lift 值">
            <ReactECharts option={liftBarOption} style={{ height: 400 }} />
          </Card>
        </Col>
      </Row>

      {/* 详情表格 */}
      <Row style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title="POI统计详情">
            <Table
              dataSource={correlation.map((c, i) => ({ ...c, key: i }))}
              columns={columns}
              pagination={false}
              size="small"
              scroll={{ x: 600 }}
            />
          </Card>
        </Col>
      </Row>

      {/* 解读卡片：帮助用户理解 Lift 值的含义 */}
      <Row style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title="解读">
            <ul>
              <li><strong>Lift &gt; 1.5</strong>：该POI类型在瑞幸门店周边显著富集，是选址的关键吸引因素</li>
              <li><strong>Lift ≈ 1.0</strong>：该POI类型在门店周边的密度与随机分布无显著差异</li>
              <li><strong>Lift &lt; 0.8</strong>：该POI类型在门店周边相对稀少，门店倾向于避开</li>
              <li><strong>关键发现</strong>：写字楼(office)、地铁站(metro)和购物中心(mall)通常是瑞幸门店选址的Top-3关联POI</li>
            </ul>
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default PoiAnalysisPage;
