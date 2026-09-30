/** 本文件统一导出各命令实现，供 CLI 与其它模块调用。 */

export { runLogin } from './login.js';
export { runListPositions, selectPosition } from './positions.js';
export { runRecommend, renderCandidates, type RecommendCandidate } from './recommend.js';
export { runOpenDetail } from './open.js';
export { runGreet } from './greet.js';
export { runRequest, type RequestKind } from './request.js';
export { findCandidateCard } from './candidate_locator.js';
