/**
 * 本文件固化智联招聘的页面地址与选择器，是页面适配的唯一来源。
 *
 * 选择器取自智联招聘 B 端推荐页的真实结构，每个键都带有用途说明；
 * 一个键给出多个选择器时按顺序降级尝试，命中即用。
 */

/**
 * 登录入口地址。
 *
 * 智联的求职者端与企业端是两套账号：企业端登录页必须从企业端入口跳转过来，
 * 直接访问 `passport.zhaopin.com` 打开的是求职者登录页，登进去企业端并不认。
 * 因此这里统一用企业端入口，未登录时它会自动跳到企业登录页并带回跳地址。
 */
export const LOGIN_URL = 'https://rd6.zhaopin.com/app/recommend';

/** 主动打招呼流程的入口页，候选人推荐列表在这里。 */
export const ENTRY_URL = 'https://rd6.zhaopin.com/app/recommend';

/** 聊天页地址，`download` 系列命令在会话列表里找人并下载附件简历。 */
export const CHAT_URL = 'https://rd6.zhaopin.com/app/im';

/** 候选人列表每次真实滚轮的滚动距离（像素）。 */
export const SCROLL_DISTANCE = 620;

/** 单次最多读取的候选人数量。 */
export const MAX_ITEMS = 100;

/** 页面选择器集合。 */
export const SELECTORS = {
  /** 职位选择弹层入口（页面右上角的「更多职位」）。 */
  positionOpen: ["a[zp-stat-id='talent_more_jobs']"],

  /** 职位选择弹层内的搜索框。 */
  positionInput: ['.job-side-selector__filter input'],

  /** 职位搜索结果项。 */
  positionItem: ['.job-side-selector__item'],

  /** 职位搜索结果里的岗位标题。 */
  positionTitle: ['.job-side-selector__title'],

  /** 职位选择弹层容器，用于确认选中岗位后弹层已关闭。 */
  positionPanel: ['.job-side-selector'],

  /** 候选人列表的滚轮落点，逐级降级到页面级。 */
  candidateList: ['.recommend-list [role="group"]', '.recommend-list', 'body'],

  /** 候选人卡片的父级容器。 */
  candidateCardParent: ['.recommend-list [role="group"]', '.recommend-list', 'body'],

  /** 候选人卡片本身。 */
  candidateCard: ['.recommend-item__inner.recommend-resume-item__inner'],

  /** 卡片内打开详情的入口（姓名区域，避开右侧打电话与打招呼按钮）。 */
  detailOpen: ['.talent-basic-info__name--inner'],

  /** 姓名区域点击无效时改点经历区域，同样远离操作按钮。 */
  detailOpenFallback: ['.resume-item__content.resume-card-exp'],

  /** 详情正文根节点。 */
  detailBody: ['.new-resume-detail--inner', '.resume-detail'],

  /** 候选人姓名。 */
  fieldName: ['.talent-basic-info__name--inner'],

  /** 候选人基本信息（年龄、经验、学历等）。 */
  fieldBasicInfo: ['.talent-basic-info__basic'],

  /** 候选人工作与教育经历。 */
  fieldEducation: ['.resume-item__content.resume-card-exp'],

  /** 候选人卡片描述区域。 */
  fieldDescription: ['.resume-item__content', '.talent-basic-info__extra--content'],

  /** 打招呼按钮（大屏与小屏两种样式）。 */
  greetButton: ['.large-screen-btn', '.small-screen-btn.is-mr-16'],

  /** 打招呼后原位置出现的「继续沟通」按钮，用于确认打招呼已生效。 */
  continueButton: ['.large-screen-btn', '.resume-btn-small', '.small-screen-btn.is-mr-16'],
} as const;

/** 打招呼按钮必须匹配的文字，避免误点同区域的打电话按钮。 */
export const GREET_BUTTON_TEXT = '打招呼';

/** 打招呼后按钮变化的文字，用于确认操作生效。 */
export const CONTINUE_BUTTON_TEXT = '继续沟通';

/** 首次打招呼时弹出的招呼语选择弹框标题。 */
export const GREET_DIALOG_TITLE = '选择招呼语';

/** 招呼语弹框里的最终发送按钮文字。 */
export const GREET_SEND_TEXT = '发送';

/** 聊天框底部操作区，索要信息和发送消息都以它为稳定范围。 */
export const CHAT_FOOTER_SELECTOR = '#im-session-detail-footer';

/** 聊天框内索要附件简历的可点击范围：以整个底部操作区为范围，再按精确文字定位按钮。 */
export const CHAT_RESUME_SCOPE_SELECTOR = CHAT_FOOTER_SELECTOR;

/** 索要附件简历按钮的文字。 */
export const REQUEST_RESUME_TEXT = '要附件简历';

/** 聊天框内索要电话按钮。 */
export const CHAT_PHONE_SELECTOR = `${CHAT_FOOTER_SELECTOR} [zp-stat-id='im_ask_for_the_phone']`;

/** 索要电话时出现的「向对方索要」二次确认项。 */
export const CHAT_PHONE_CONFIRM_SELECTOR =
  ".km-popover.im-ask-for-contact__popper a[zp-stat-id='render_time_track']";

/** 聊天框内索要微信按钮。 */
export const CHAT_WECHAT_SELECTOR = `${CHAT_FOOTER_SELECTOR} [zp-stat-id='im_ask_for_wx_open']`;

/** 聊天框输入框。 */
export const CHAT_INPUT_SELECTOR = `${CHAT_FOOTER_SELECTOR} textarea[placeholder='从这里开启对话...']`;

/** 聊天框关闭按钮。 */
export const CHAT_CLOSE_SELECTOR = '.im-widget-session__close';

/** 聊天框头部的候选人姓名，用于确认打开的是目标会话。 */
export const CHAT_NAME_SELECTORS = [
  '.im-candidate__item.im-candidate__name',
  '.im-three-list__panel--name',
] as const;

/** 打开聊天框用的「继续沟通」按钮（打招呼成功后原按钮会变成它）。 */
export const CHAT_OPEN_SELECTORS = [
  '.large-screen-btn',
  '.resume-btn-small',
  '.small-screen-btn.is-mr-16',
] as const;

/** 聊天页左侧会话列表里的单个会话条目。 */
export const SESSION_ITEM_SELECTOR = '.im-session-item';

/** 会话条目里的候选人姓名。 */
export const SESSION_NAME_SELECTOR = '.im-session-item__name-title';

/** 会话列表的滚动容器（虚拟列表），用于加载更多会话。 */
export const SESSION_LIST_SCROLL_SELECTOR = '.im-session-list__virtual';

/**
 * 聊天页右侧简历详情栏里「对方发来的附件简历」入口。
 * 带 `is-disabled` 时是「附件简历索要中」（我方已索要、对方未发），不可点击下载。
 */
export const ATTACH_RESUME_CARD_SELECTOR = '.newest-attach-resume:not(.is-disabled)';

/** 附件简历下载直链的域名特征，用于识别点击后新开的标签页。 */
export const ATTACH_DOWNLOAD_URL_MARKER = 'attachment.zhaopin.com';
