try {
  var savedTheme = localStorage.getItem('linkget.theme');
  if (savedTheme === 'light' || savedTheme === 'dark') {
    var meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.content = savedTheme;
  }
  var savedLang = localStorage.getItem('linkget.lang');
  if (savedLang === 'zh-Hans') {
    document.documentElement.lang = 'zh-Hans';
  }
} catch (e) {}
