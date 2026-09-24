/**
 * @fileoverview Hooks 模块统一导出入口
 *
 * 职责：聚合并重新导出所有自定义 Hook，提供统一的导入路径。
 * 使用者只需 `import { useApi, useCities, ... } from '../hooks'` 即可访问所有 Hook。
 *
 * 导出清单：
 * - useApi / useMutation：通用 API 请求封装
 * - useCities：城市列表及统计
 * - useTimeline / useHeatmap：门店时间轴及热力图数据
 * - usePoiAnalysis：POI 关联分析
 * - useComparison：品牌对比分析
 * - usePrediction：选址预测评分
 */

export { useApi, useMutation } from './useApi';
export { useCities } from './useCities';
export type { CityInfo } from './useCities';
export { useTimeline, useHeatmap } from './useStoreData';
export { usePoiAnalysis } from './usePoiAnalysis';
export { useComparison } from './useComparison';
export { usePrediction } from './usePrediction';
