import reactHooks from 'eslint-plugin-react-hooks';

export default [
  {
    ignores: ['dist/**', 'node_modules/**'],
  },
  {
    files: ['src/**/*.{js,jsx}', 'tests/**/*.js', '*.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {ecmaFeatures: {jsx: true}},
      globals: {
        console: 'readonly', document: 'readonly', fetch: 'readonly',
        FormData: 'readonly', localStorage: 'readonly', prompt: 'readonly',
        URL: 'readonly', process: 'readonly',
      },
    },
    plugins: {'react-hooks': reactHooks},
    rules: {
      ...reactHooks.configs.recommended.rules,
      'no-undef': 'error',
      // JSX component references require eslint-plugin-react's jsx-uses-vars;
      // hooks and undefined globals remain enforced without adding that plugin.
      'no-unused-vars': 'off',
    },
  },
];
