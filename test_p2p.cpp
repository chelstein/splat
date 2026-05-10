/*  test_p2p.cpp - minimal CLI wrapper around itwom3.0.cpp's
 *  point_to_point_ITM.
 *
 *  USAGE
 *      test_p2p < input.json
 *
 *  STDIN (one JSON object per line is also fine; we parse the whole
 *  buffer):
 *      {
 *        "profile":      [np, xi, e0, e1, ..., enp],
 *        "tx_height_m":  30.0,
 *        "rx_height_m":  10.0,
 *        "frequency_mhz": 100.0,
 *        "eps_dielect":   15.0,
 *        "sgm_conduct":    0.005,
 *        "eno_ns_surfref": 301.0,
 *        "radio_climate":  5,
 *        "pol":            1,
 *        "conf":           0.5,
 *        "rel":            0.5
 *      }
 *
 *  STDOUT
 *      {
 *        "dbloss":     <total path loss, dB>,
 *        "errnum":     <kwx warning level>,
 *        "strmode":    "<mode string>",
 *        "fs":         <free-space loss, dB>,
 *        "dist_m":     <profile length, m>,
 *        "splat_version": "<version string>"
 *      }
 *
 *  This is a straight thunk into the same point_to_point_ITM the splat
 *  binary exposes for site reports.  Genoa's JS port (chelstein/itmlogic
 *  src/engine/coverage/itm_v122) targets the same function - this CLI
 *  is the canonical numerical reference for the splat-vs-JS bake-off
 *  harness.
 *
 *  No external JSON library: a tiny in-file parser handles the subset
 *  of JSON we need (object with numeric keys + a numeric array).
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <string>
#include <vector>
#include <cmath>
#include <cctype>

/* point_to_point_ITM is defined in itwom3.0.cpp.  Forward-declare it
 * with the exact signature so we don't need any header. */
extern void point_to_point_ITM(
    double elev[], double tht_m, double rht_m,
    double eps_dielect, double sgm_conductivity, double eno_ns_surfref,
    double frq_mhz, int radio_climate, int pol,
    double conf, double rel, double &dbloss,
    char *strmode, int &errnum);

extern double ITWOMVersion();

/* ---------- minimal JSON-ish reader ----------------------------------
 *
 * Reads stdin into a string, then walks it pulling out:
 *    "<key>": <number>      -> into named scalars
 *    "<key>": [<n>, <n>...] -> into one numeric vector (the profile)
 *
 * Robust against whitespace and arbitrary key ordering.  Not robust
 * against nested objects or escaped strings - we don't need them. */

static std::string slurp_stdin() {
    std::string s;
    char buf[4096];
    while (size_t n = fread(buf, 1, sizeof(buf), stdin)) s.append(buf, n);
    return s;
}

static const char* skip_ws(const char* p) {
    while (*p && isspace((unsigned char)*p)) ++p;
    return p;
}

/* Find "<key>" in `body`, return pointer just past the matching colon
 * (i.e. at the start of the value), or nullptr if not found. */
static const char* find_key(const std::string& body, const char* key) {
    std::string needle = "\"";
    needle += key;
    needle += "\"";
    size_t pos = body.find(needle);
    if (pos == std::string::npos) return nullptr;
    const char* p = body.c_str() + pos + needle.size();
    p = skip_ws(p);
    if (*p != ':') return nullptr;
    ++p;
    return skip_ws(p);
}

static bool read_number(const char*& p, double& out) {
    char* end = nullptr;
    out = strtod(p, &end);
    if (end == p) return false;
    p = end;
    return true;
}

static bool read_field_double(const std::string& body, const char* key,
                              double dflt, double& out) {
    const char* p = find_key(body, key);
    if (!p) { out = dflt; return false; }
    return read_number(p, out);
}

static bool read_field_int(const std::string& body, const char* key,
                           int dflt, int& out) {
    double d;
    if (!read_field_double(body, key, dflt, d)) { out = dflt; return false; }
    out = (int)d;
    return true;
}

static bool read_field_array(const std::string& body, const char* key,
                             std::vector<double>& out) {
    const char* p = find_key(body, key);
    if (!p || *p != '[') return false;
    ++p;
    p = skip_ws(p);
    while (*p && *p != ']'){
        double v;
        if (!read_number(p, v)) return false;
        out.push_back(v);
        p = skip_ws(p);
        if (*p == ',') { ++p; p = skip_ws(p); }
    }
    return *p == ']';
}

/* Tiny printf-with-escape helper for stdout JSON. */
static void print_json_string(const char* s) {
    putchar('"');
    for (; *s; ++s) {
        if (*s == '"' || *s == '\\') { putchar('\\'); putchar(*s); }
        else if ((unsigned char)*s < 32) {
            printf("\\u%04x", (unsigned char)*s);
        } else putchar(*s);
    }
    putchar('"');
}

int main() {
    std::string body = slurp_stdin();

    std::vector<double> profile;
    if (!read_field_array(body, "profile", profile) || profile.size() < 3) {
        fprintf(stderr, "{\"error\":\"profile array missing or too short\"}\n");
        return 2;
    }

    double tht, rht, eps, sgm, en0, fmhz, conf, rel;
    int    klim, pol;
    read_field_double(body, "tx_height_m",     30.0,  tht);
    read_field_double(body, "rx_height_m",     10.0,  rht);
    read_field_double(body, "eps_dielect",     15.0,  eps);
    read_field_double(body, "sgm_conduct",     0.005, sgm);
    read_field_double(body, "eno_ns_surfref",  301.0, en0);
    read_field_double(body, "frequency_mhz",   100.0, fmhz);
    read_field_double(body, "conf",            0.5,   conf);
    read_field_double(body, "rel",             0.5,   rel);
    read_field_int   (body, "radio_climate",   5,     klim);
    read_field_int   (body, "pol",             1,     pol);

    double dbloss   = 0.0;
    int    errnum   = 0;
    char   strmode[256];
    strmode[0] = '\0';

    point_to_point_ITM(
        profile.data(), tht, rht, eps, sgm, en0, fmhz,
        klim, pol, conf, rel, dbloss, strmode, errnum);

    /* Re-derive fs from the profile + frequency for the response so
     * callers can sanity-check excess vs fsl. */
    int    np      = (int)profile[0];
    double xi      = profile[1];
    double dist_m  = (double)np * xi;
    double dist_km = dist_m / 1000.0;
    double fs      = 32.45 + 20.0 * log10(fmhz)
                   + 20.0 * log10(dist_km > 0.001 ? dist_km : 0.001);

    printf("{");
    printf("\"dbloss\":%.6f,",   dbloss);
    printf("\"errnum\":%d,",     errnum);
    printf("\"strmode\":");      print_json_string(strmode); putchar(',');
    printf("\"fs\":%.6f,",       fs);
    printf("\"dist_m\":%.3f,",   dist_m);
    printf("\"splat_version\":%.6f", ITWOMVersion());
    printf("}\n");
    return 0;
}
