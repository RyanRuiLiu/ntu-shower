export default defineAppConfig({
  pages: ['pages/index/index'],
  window: {
    backgroundTextStyle: 'light',
    // 瓷砖白：与 .page 背景一致，避免下拉时露出默认白底造成闪动
    navigationBarBackgroundColor: '#f4f7f8',
    navigationBarTitleText: '喷头状态',
    navigationBarTextStyle: 'black',
  },
})
