//----------------------------------------------------------------------------------------------------------------------------------
// scope_control.c - PC/AI remote control commands for FNIRSI 1013D
// Added by WorkBuddy: implements real #SET / #KEY parameter changes by calling
// the firmware's own low level setters (fpga_set_*) and screen refresh functions
// (scope_*). Nothing here fakes touch coordinates, so it cannot hang waiting for
// a physical touch release.
//
// Protocol (all commands terminated by \n):
//   #SET,RUN            -> run
//   #SET,STOP           -> stop
//   #SET,SINGLE         -> single shot
//   #SET,AUTOSET        -> auto setup
//   #SET,TDIV,<i>       -> time per div index 0..34 (see time_div_texts)
//   #SET,CH1,VDIV,<i>   -> CH1 volts/div index 0..6 (1x probe row)
//   #SET,CH2,VDIV,<i>   -> CH2 volts/div index 0..6
//   #SET,CH1,ON|OFF     -> channel enable
//   #SET,CH2,ON|OFF
//   #SET,CH1,AC|DC      -> coupling
//   #SET,CH2,AC|DC
//   #SET,CH1,PROBE,<i>  -> probe magnification index 0..6 (0.5x,1x,10x,20x,50x,100x,1000x)
//   #SET,CH2,PROBE,<i>
//   #SET,TRIG,CH1|CH2   -> trigger source
//   #SET,TRIG,AUTO|NORMAL|SINGLE
//   #SET,TRIG,RISE|FALL -> trigger edge
//   #SET,TRIG,LEVEL,<y> -> trigger level screen Y 6..396 (like dragging the marker)
//   #SET,CH1,POS,<y>    -> CH1 trace vertical position 6..396 (like dragging)
//   #SET,CH2,POS,<y>    -> CH2 trace vertical position 6..396
//   #KEY,<hex>          -> legacy web-app key code (mapped to the above actions)
//----------------------------------------------------------------------------------------------------------------------------------

#include "types.h"
#include "variables.h"
#include "fnirsi_1013d_scope.h"
#include "menu.h"
#include "scope_functions.h"
#include "statemachine.h"
#include "fpga_control.h"
#include "display_control.h"
#include "PC_interface.h"
#include "usb_interface.h"

#include <string.h>

// external helpers provided by the existing firmware
extern void usb_CDC_send_text(const char *text);
extern sysparam SysParam;   // PC_interface.h keeps this declaration commented out
extern uint8 chinese_enabled;  // WorkBuddy: 1=Chinese UI, 0=English UI

//----------------------------------------------------------------------------
// small string helpers (no libc reliance beyond strcmp/strncmp)
//----------------------------------------------------------------------------
static int ieq(const char *a, const char *b)
{
    return my_strcasecmp(a, b) == 0;
}

static int parse_dec(const char *s)
{
    int v = 0;
    while (*s >= '0' && *s <= '9') { v = v * 10 + (*s - '0'); s++; }
    return v;
}

// split a comma separated command into at most 4 tokens (mutates the buffer)
static int split_tokens(char *buf, char *tok[], int maxtok)
{
    int n = 0;
    tok[n++] = buf;
    while (*buf && n < maxtok)
    {
        if (*buf == ',') { *buf = 0; tok[n++] = buf + 1; }
        buf++;
    }
    return n;
}

//----------------------------------------------------------------------------
// SysParam synchronisation - keep the PC mirror in sync with real settings
//----------------------------------------------------------------------------
static void sync_sysparam(void)
{
    SysParam.run_state = scopesettings.runstate;
    SysParam.ch2_enabled = scopesettings.channel2.enable;

    // time_zoom in SysParam is the inverted index used by the web app:
    // time_zoom 2 = 5ns ... 30 = 10s, i.e. 34 - timeperdiv
    {
        int t = (int)scopesettings.timeperdiv;
        int z = 34 - t;
        if (z < 0) z = 0;
        if (z > 255) z = 255;
        SysParam.time_zoom = (uint8)z;
    }

    SysParam.trigger_channel = (uint8)scopesettings.triggerchannel;
    SysParam.trigger_mode = (uint8)(scopesettings.triggermode == 2 ? 1 : 0);
    SysParam.trigger_edge = (uint8)scopesettings.triggeredge;

    // volts/div mirror: web app uses 4..13 for index 0..6 at 1x probe
    {
        int c1 = 4 + (int)scopesettings.channel1.displayvoltperdiv;
        int c2 = 4 + (int)scopesettings.channel2.displayvoltperdiv;
        SysParam.ch1_volt_zoom = (uint8)(c1 > 255 ? 255 : c1);
        SysParam.ch2_volt_zoom = (uint8)(c2 > 255 ? 255 : c2);
    }
    SysParam.ch1_coupling = scopesettings.channel1.coupling;
    SysParam.ch2_coupling = scopesettings.channel2.coupling;

    // probe magnification 0..6 -> web app 1x=0, 10x=1, 100x=2
    {
        uint8 m = scopesettings.channel1.magnification;
        SysParam.ch1_probe = (m >= 2 && m <= 3) ? 1 : (m >= 4 ? 2 : 0);
        m = scopesettings.channel2.magnification;
        SysParam.ch2_probe = (m >= 2 && m <= 3) ? 1 : (m >= 4 ? 2 : 0);
    }

    // trigger level (ADC counts 0..255, 128 = trace center) as computed by
    // fpga_set_trigger_level(); keeps #READ byte[72] truthful
    {
        int lvl = (int)scopesettings.triggerlevel;
        if (lvl < 0) lvl = 0;
        if (lvl > 255) lvl = 255;
        SysParam.trigger_level = (uint8)lvl;
    }

    // trace vertical positions (screen Y, 16-bit little endian)
    SysParam.ch1_vert_position[0] = (int8)(scopesettings.channel1.traceposition & 0xFF);
    SysParam.ch1_vert_position[1] = (int8)((scopesettings.channel1.traceposition >> 8) & 0xFF);
    SysParam.ch2_vert_position[0] = (int8)(scopesettings.channel2.traceposition & 0xFF);
    SysParam.ch2_vert_position[1] = (int8)((scopesettings.channel2.traceposition >> 8) & 0xFF);
}

static void refresh_all(void)
{
    sync_sysparam();
    scope_run_stop_text();
    scope_channel_settings(&scopesettings.channel1, 0);
    scope_channel_settings(&scopesettings.channel2, 0);
    scope_trigger_settings(0);
}

//----------------------------------------------------------------------------
// individual setters
//----------------------------------------------------------------------------
static void ctl_run(void)
{
    if (!scopesettings.runstate)
    {
        scope_run_stop_button(1);
        scopesettings.runstate = 1;
        scope_acqusition_settings(0);
        match_volt_per_div_settings(&scopesettings.channel1);
        match_volt_per_div_settings(&scopesettings.channel2);
        scope_run_stop_button(0);
    }
    refresh_all();
}

static void ctl_stop(void)
{
    if (scopesettings.runstate)
    {
        scope_run_stop_button(1);
        scopesettings.runstate = 0;
        scope_run_stop_button(0);
    }
    refresh_all();
}

static void ctl_single(void)
{
    set_trigger_mode(1);   // 1 = single
    refresh_all();
}

static void ctl_timebase(int idx)
{
    if (idx < 0) idx = 0;
    if (idx > 34) idx = 34;
    scopesettings.timeperdiv = (uint8)idx;
    scope_set_timebase();      // applies to FPGA, sample rate and screen
    refresh_all();
}

static void ctl_vdiv(PCHANNELSETTINGS settings, int idx)
{
    if (idx < 0) idx = 0;
    if (idx > 6) idx = 6;
    settings->displayvoltperdiv = (uint8)idx;
    match_volt_per_div_settings(settings);   // writes FPGA + offset
    scope_channel_settings(settings, 0);
    scope_channel_sensitivity_select(settings);
    refresh_all();
}

static void ctl_channel_enable(PCHANNELSETTINGS settings, int on)
{
    if ((settings->enable ? 1 : 0) == on) { refresh_all(); return; }
    settings->enable = (uint8)on;
    fpga_swap_trigger_channel();
    scope_calculate_trigger_vertical_position();
    scope_channel_enable_select(settings);
    scope_channel_settings(settings, 0);
    scope_trigger_settings(0);
    refresh_all();
}

static void ctl_channel_coupling(PCHANNELSETTINGS settings, int ac)
{
    settings->coupling = (uint8)ac;
    if (ac) settings->dcoffset = 0;
    fpga_set_channel_coupling(settings);
    fpga_set_channel_offset(settings);
    scope_channel_coupling_select(settings);
    scope_channel_settings(settings, 0);
    refresh_all();
}

static void ctl_channel_probe(PCHANNELSETTINGS settings, int idx)
{
    if (idx < 0) idx = 0;
    if (idx > 6) idx = 6;
    settings->magnification = (uint8)idx;
    scope_channel_probe_magnification_select(settings);
    scope_channel_settings(settings, 0);
    scope_channel_sensitivity_select(settings);
    match_volt_per_div_settings(settings);
    refresh_all();
}

static void ctl_trigger_source(int ch)
{
    // 0 = CH1, 1 = CH2, 2 = EXT
    if (ch < 0) ch = 0;
    if (ch > 2) ch = 2;
    scopesettings.triggerchannel = (uint8)ch;
    if (ch != 2) fpga_set_trigger_channel();
    scope_calculate_trigger_vertical_position();
    scope_trigger_channel_select();
    scope_trigger_settings(0);
    refresh_all();
}

static void ctl_trigger_mode(int mode)
{
    // 0=AUTO, 1=SINGLE, 2=NORMAL  (firmware's own setter)
    set_trigger_mode((uint32)mode);
    refresh_all();
}

static void ctl_trigger_edge(int falling)
{
    scopesettings.triggeredge = (uint8)falling;
    fpga_set_trigger_edge();
    scope_trigger_edge_select();
    scope_trigger_settings(0);
    refresh_all();
}

// trigger level by screen Y position (same range as touch drag: 6..396)
static void ctl_trigger_level(int y)
{
    if (y < 6) y = 6;
    if (y > 396) y = 396;
    scopesettings.triggerverticalposition = (uint16)y;
    fpga_set_trigger_level();          // apply to FPGA immediately
    refresh_all();
}

// channel trace vertical position by screen Y (same range as touch drag).
// Mirrors the touch behaviour: when the trigger runs on this channel the
// trigger marker follows the trace so the trigger voltage does not change.
static void ctl_channel_pos(PCHANNELSETTINGS settings, int y)
{
    int old = (int)settings->traceposition;
    if (y < 6) y = 6;
    if (y > 396) y = 396;
    settings->traceposition = (uint16)y;

    if ((settings == &scopesettings.channel1 && scopesettings.triggerchannel == 0) ||
        (settings == &scopesettings.channel2 && scopesettings.triggerchannel == 1))
    {
        int tp = (int)scopesettings.triggerverticalposition + (y - old);
        if (tp < 6) tp = 6;
        if (tp > 396) tp = 396;
        scopesettings.triggerverticalposition = (uint16)tp;
    }

    fpga_set_channel_offset(settings);
    fpga_set_trigger_level();          // keep FPGA trigger level in sync
    refresh_all();
}

//----------------------------------------------------------------------------
// #MEAS - dump the firmware's own on-screen measurement values.
// We reuse the exact measurementfunctions[] the screen uses, so each value is
// byte-for-byte identical to what the user sees on the scope panel. The PC does
// NOT have to recompute anything (and therefore cannot disagree with the screen,
// e.g. on Vavg / Vrms which use the firmware's dcoffset-corrected samples).
//----------------------------------------------------------------------------
extern const MEASUREMENTFUNCTION measurementfunctions[12];

// stable ASCII keys (screen order), independent of the firmware's "Freg" typo
static const char *meas_keys[12] = {
    "Vmax", "Vmin", "Vavg", "Vrms", "Vpp", "Vp",
    "Freq", "Cycle", "Time+", "Time-", "Duty+", "Duty-"
};

// self-contained prefix compare (avoids relying on an external strncmp)
static int cprefix(const char *s, const char *pre, int n)
{
    while (n-- > 0)
    {
        if (*s != *pre) return 0;
        s++; pre++;
    }
    return 1;
}

static void send_meas_channel(const char *chlabel, PCHANNELSETTINGS ch)
{
    int i;
    usb_CDC_send_text("#MEAS,");
    usb_CDC_send_text(chlabel);
    for (i = 0; i < 12; i++)
    {
        measurementtext[0] = 0;
        measurementfunctions[i](ch);          // fills global measurementtext
        usb_CDC_send_text(",");
        usb_CDC_send_text(meas_keys[i]);
        usb_CDC_send_text("=");
        usb_CDC_send_text(measurementtext);    // verbatim screen string (incl unit)
    }
    usb_CDC_send_text("\n");
}

//----------------------------------------------------------------------------
// main dispatcher. Returns 1 if handled, 0 otherwise.
// buf is a NUL terminated command WITHOUT the leading '#'.
//----------------------------------------------------------------------------
int scope_control_dispatch(char *cmd)
{
    char *tok[5];
    int n;

    // #MEAS[,CH1|CH2] : dump on-screen measurements; default = enabled channels
    if (cprefix(cmd, "MEAS", 4))
    {
        char *p = cmd + 4;
        if (*p == ',') p++;
        if (ieq(p, "CH1"))
        {
            send_meas_channel("CH1", &scopesettings.channel1);
        }
        else if (ieq(p, "CH2"))
        {
            send_meas_channel("CH2", &scopesettings.channel2);
        }
        else
        {
            int sent = 0;
            if (scopesettings.channel1.enable) { send_meas_channel("CH1", &scopesettings.channel1); sent = 1; }
            if (scopesettings.channel2.enable) { send_meas_channel("CH2", &scopesettings.channel2); sent = 1; }
            if (!sent) usb_CDC_send_text("#ERR,MEAS\n");
        }
        return 1;
    }

    // cmd points at e.g. "SET,RUN" or "KEY,F"
    if (strncmp(cmd, "SET", 3) == 0)
    {
        // skip "SET"
        char *p = cmd + 3;
        if (*p == ',') p++;
        n = split_tokens(p, tok, 5);
        if (n <= 0) return 0;

        // WorkBuddy: language switch
        if (ieq(tok[0], "LANG") && n >= 2)
        {
            if (ieq(tok[1], "CN") || ieq(tok[1], "ZH"))      chinese_enabled = 1;
            else if (ieq(tok[1], "EN"))                      chinese_enabled = 0;
            else if (ieq(tok[1], "TOGGLE"))                  chinese_enabled ^= 1;
            refresh_all();
            usb_CDC_send_text(chinese_enabled ? "#OK,CN\n" : "#OK,EN\n");
            return 1;
        }
        if (ieq(tok[0], "RUN"))        { ctl_run();    usb_CDC_send_text("#OK,RUN\n");    return 1; }
        if (ieq(tok[0], "STOP"))       { ctl_stop();   usb_CDC_send_text("#OK,STOP\n");   return 1; }
        if (ieq(tok[0], "SINGLE"))     { ctl_single(); usb_CDC_send_text("#OK,SINGLE\n"); return 1; }
        if (ieq(tok[0], "AUTOSET"))
        {
            // reuse the firmware's autosetup by forcing the known entry point
            // (autoset is implemented in scope_functions.c)
            scope_do_auto_setup();
            refresh_all();
            usb_CDC_send_text("#OK,AUTOSET\n");
            return 1;
        }
        if (ieq(tok[0], "TDIV") && n >= 2)
        {
            ctl_timebase(parse_dec(tok[1]));
            usb_CDC_send_text("#OK,TDIV\n");
            return 1;
        }
        if ((ieq(tok[0], "CH1") || ieq(tok[0], "CH2")) && n >= 2)
        {
            PCHANNELSETTINGS ch = ieq(tok[0], "CH1") ? &scopesettings.channel1 : &scopesettings.channel2;
            const char *which = tok[0];
            if (ieq(tok[1], "ON"))       { ctl_channel_enable(ch, 1);  usb_CDC_send_text("#OK,ON\n");   return 1; }
            if (ieq(tok[1], "OFF"))      { ctl_channel_enable(ch, 0);  usb_CDC_send_text("#OK,OFF\n");  return 1; }
            if (ieq(tok[1], "AC"))       { ctl_channel_coupling(ch, 1);usb_CDC_send_text("#OK,AC\n");   return 1; }
            if (ieq(tok[1], "DC"))       { ctl_channel_coupling(ch, 0);usb_CDC_send_text("#OK,DC\n");   return 1; }
            if (ieq(tok[1], "VDIV") && n >= 3) { ctl_vdiv(ch, parse_dec(tok[2])); usb_CDC_send_text("#OK,VDIV\n"); return 1; }
            if (ieq(tok[1], "PROBE") && n >= 3){ ctl_channel_probe(ch, parse_dec(tok[2])); usb_CDC_send_text("#OK,PROBE\n"); return 1; }
            if (ieq(tok[1], "POS") && n >= 3)  { ctl_channel_pos(ch, parse_dec(tok[2])); usb_CDC_send_text("#OK,POS\n"); return 1; }
            (void)which;
            return 0;
        }
        if (ieq(tok[0], "TRIG") && n >= 2)
        {
            if (ieq(tok[1], "CH1"))       { ctl_trigger_source(0); usb_CDC_send_text("#OK,TRIG\n"); return 1; }
            if (ieq(tok[1], "CH2"))       { ctl_trigger_source(1); usb_CDC_send_text("#OK,TRIG\n"); return 1; }
            if (ieq(tok[1], "AUTO"))      { ctl_trigger_mode(0);   usb_CDC_send_text("#OK,TRIG\n"); return 1; }
            if (ieq(tok[1], "SINGLE"))    { ctl_trigger_mode(1);   usb_CDC_send_text("#OK,TRIG\n"); return 1; }
            if (ieq(tok[1], "NORMAL"))    { ctl_trigger_mode(2);   usb_CDC_send_text("#OK,TRIG\n"); return 1; }
            if (ieq(tok[1], "RISE"))      { ctl_trigger_edge(0);   usb_CDC_send_text("#OK,TRIG\n"); return 1; }
            if (ieq(tok[1], "FALL"))      { ctl_trigger_edge(1);   usb_CDC_send_text("#OK,TRIG\n"); return 1; }
            if (ieq(tok[1], "LEVEL") && n >= 3) { ctl_trigger_level(parse_dec(tok[2])); usb_CDC_send_text("#OK,TRIG\n"); return 1; }
            return 0;
        }
        return 0;
    }

    // legacy web-app #KEY codes -> map to real actions
    // Two notations are accepted:
    //   #KEY,F    single character  -> ASCII code (0x46)  (readable form)
    //   #KEY,46   two hex digits    -> 0x46              (original protocol)
    if (strncmp(cmd, "KEY", 3) == 0)
    {
        char *p = cmd + 3;
        uint32 code;
        if (*p == ',') p++;
        if (p[0] && p[1] == 0)
        {
            // single character -> ASCII code
            code = (uint32)(uint8)p[0];
        }
        else
        {
            // parse hex
            code = 0;
            while (*p)
            {
                char c = *p++;
                uint32 v;
                if (c >= '0' && c <= '9') v = (uint32)(c - '0');
                else if (c >= 'A' && c <= 'F') v = (uint32)(c - 'A' + 10);
                else if (c >= 'a' && c <= 'f') v = (uint32)(c - 'a' + 10);
                else continue;
                code = (code << 4) | v;
            }
        }
        switch (code)
        {
            case 0x46: // 'F' RUN/STOP
                if (scopesettings.runstate) ctl_stop(); else ctl_run();
                usb_CDC_send_text("#OK,KEY\n"); return 1;
            case 0x41: // 'A' AUTOSET
                scope_do_auto_setup(); refresh_all(); usb_CDC_send_text("#OK,KEY\n"); return 1;
            case 0x33: // '3' SINGLE
                ctl_single(); usb_CDC_send_text("#OK,KEY\n"); return 1;
            case 0x42: // 'B' CH1 VDIV up (sensitivity down)
                ctl_vdiv(&scopesettings.channel1, (int)scopesettings.channel1.displayvoltperdiv + 1);
                usb_CDC_send_text("#OK,KEY\n"); return 1;
            case 0x34: // '4' CH1 VDIV down
                ctl_vdiv(&scopesettings.channel1, (int)scopesettings.channel1.displayvoltperdiv - 1);
                usb_CDC_send_text("#OK,KEY\n"); return 1;
            case 0x43: // 'C' time base faster (lower index)
                ctl_timebase((int)scopesettings.timeperdiv + 1);
                usb_CDC_send_text("#OK,KEY\n"); return 1;
            case 0x44: // 'D' time base slower
                ctl_timebase((int)scopesettings.timeperdiv - 1);
                usb_CDC_send_text("#OK,KEY\n"); return 1;
            case 0x38: // '8' horizontal pos +
            case 0x36: // '6' horizontal pos -
            case 0x30: // '0'
            case 0x31: // '1'
            case 0x35: // '5' select channel
            case 0x39: // '9'
                // menu navigation / knob fine moves are intentionally no-ops
                usb_CDC_send_text("#OK,KEY\n"); return 1;
            default:
                usb_CDC_send_text("#ERR,KEY\n"); return 1;
        }
    }

    return 0;
}
