/* 自动生成: 中文 16x16 点阵字库 + 翻译表 - 请勿手工修改 */
#ifndef CHINESE_FONT_H
#define CHINESE_FONT_H
#include "types.h"
#define ZH_GLYPH_COUNT 208
#define ZH_GLYPH_BYTES 32
extern const uint16 zh_codepoints[ZH_GLYPH_COUNT];
extern const uint8 zh_glyphs[ZH_GLYPH_COUNT][ZH_GLYPH_BYTES];
/* 翻译表: 英文原文 -> UTF-8 中文字符串 */
#define ZH_TRANS_COUNT 194
typedef struct { const int8 *en; const int8 *zh; } ZH_TRANS;
extern const ZH_TRANS zh_trans[ZH_TRANS_COUNT];
extern uint8 chinese_enabled;  /* 1=中文界面, 0=英文 */
#endif