/* libcurl shim for wick (bind-mounted over the flatpak runtime's libcurl.so.4 inside the
 * namespace only). Sober checks Roblox's certificates against a CA list of its own; this makes every
 * transfer use WICK_CA_BUNDLE (the runtime bundle + mockblox's CA) instead. Everything else goes to
 * the real libcurl (DT_NEEDED), unchanged. CURLSHIM_LOG=1 logs the TLS options it rewrites. */
#define _GNU_SOURCE
#include <curl/curl.h>
#include <dlfcn.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef WICK_CA_BUNDLE
#error "build with -DWICK_CA_BUNDLE=\"/path/ca-bundle.pem\""
#endif

static void note(const char *fmt, long opt) {
    static int on = -1;
    if (on < 0) on = getenv("CURLSHIM_LOG") != NULL;
    if (on) fprintf(stderr, fmt, opt);
}

#undef curl_easy_setopt
CURLcode curl_easy_setopt(CURL *h, CURLoption opt, ...) {
    static CURLcode (*real)(CURL *, CURLoption, ...);
    if (!real) real = (CURLcode (*)(CURL *, CURLoption, ...))dlsym(RTLD_NEXT, "curl_easy_setopt");
    va_list ap;
    va_start(ap, opt);
    CURLcode rc;
    switch (opt) {
    case CURLOPT_CAINFO:
    case CURLOPT_CAINFO_BLOB:
    case CURLOPT_CAPATH:
        (void)va_arg(ap, void *);
        note("curlshim: CA option %ld -> wick bundle\n", opt);
        rc = real(h, CURLOPT_CAINFO, WICK_CA_BUNDLE);
        break;
    case CURLOPT_PINNEDPUBLICKEY:
        (void)va_arg(ap, void *);
        note("curlshim: dropped pinned public key (%ld)\n", opt);
        rc = CURLE_OK;
        break;
    case CURLOPT_SSL_CTX_FUNCTION:
    case CURLOPT_SSL_VERIFYPEER:
    case CURLOPT_SSL_VERIFYHOST:
    case CURLOPT_SSL_OPTIONS:
        note("curlshim: TLS option %ld set (passed through)\n", opt);
        /* fall through */
    default:
        if (opt < CURLOPTTYPE_OBJECTPOINT) rc = real(h, opt, va_arg(ap, long));
        else if (opt < CURLOPTTYPE_OFF_T) rc = real(h, opt, va_arg(ap, void *));
        else if (opt < CURLOPTTYPE_BLOB) rc = real(h, opt, va_arg(ap, curl_off_t));
        else rc = real(h, opt, va_arg(ap, void *));
    }
    va_end(ap);
    return rc;
}
