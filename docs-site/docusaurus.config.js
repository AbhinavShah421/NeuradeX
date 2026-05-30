// @ts-check
const { themes } = require('prism-react-renderer');

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'NeuradeX Docs',
  tagline: 'AI-powered stock trading platform — backend API & microservice reference',
  favicon: 'img/logo.svg',

  url: 'http://localhost:3001',
  baseUrl: '/neuradex/dev/',

  onBrokenLinks: 'warn',
  onBrokenMarkdownLinks: 'warn',

  i18n: { defaultLocale: 'en', locales: ['en'] },

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          sidebarPath: require.resolve('./sidebars.js'),
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      }),
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      colorMode: {
        defaultMode: 'dark',
        disableSwitch: false,
      },
      navbar: {
        title: 'NeuradeX',
        logo: { alt: 'NeuradeX', src: 'img/logo.svg' },
        items: [
          { type: 'docSidebar', sidebarId: 'docsSidebar', position: 'left', label: 'Docs' },
          { href: '/neuradex/backend/docs', label: 'Live API (Swagger)', position: 'right' },
          { href: 'https://github.com/AbhinavShah421/NeuradeX', label: 'GitHub', position: 'right' },
        ],
      },
      footer: {
        style: 'dark',
        copyright: `NeuradeX © ${new Date().getFullYear()} · Built with Docusaurus`,
      },
      prism: {
        theme: themes.github,
        darkTheme: themes.dracula,
        additionalLanguages: ['bash', 'json', 'python', 'java', 'typescript'],
      },
    }),
};

module.exports = config;
