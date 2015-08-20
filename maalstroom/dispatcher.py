import llog

import asyncio
import importlib
import logging

import client_engine as cengine
import enc
import maalstroom
import maalstroom.templates as templates
import mbase32
import multipart

log = logging.getLogger(__name__)

class MaalstroomDispatcher(object):
    def __init__(self, handler, inq, outq):
        self.node = handler.node
        self.handler = handler

        self.inq = inq
        self.outq = outq

        self._accept_charset = None

    @property
    def connection_count(self):
        return len(self.node.chord_engine.peers)

    @property
    def latest_version_number(self):
        client_engine = maalstroom.client_engine
        if not client_engine:
            return None
        return client_engine.latest_version_number

    def send_response(self, code):
        # Maybe this should go through queue but then is less efficient and
        # this seems to work and is thread safe as we synchronized, it is only
        # a matter of if these are blocking calls, which i haven't checked in
        # the python source, but, in Linux sockets, there is no blocking
        # write in essence (buffered by kernel) so this should be find and
        # even optimal -- until we switch to asyncio http server. Abstracting
        # it from handler just in case and to make switching easier as
        # refactoring a non statically compiled language like Python is the
        # definition of pain.
        self.handler.send_response(code)

    def send_header(self, key, value):
        self.handler.send_header(key, value)

    def end_headers(self):
        self.handler.end_headers()

    def write(self, data):
        assert type(data) in (bytes, bytearray)
        self.outq.put(data)

    def flush(self):
        self.outq.put(maalstroom.Flush)

    def finish_response(self):
        self.outq.put(None)

    @asyncio.coroutine
    def do_GET(self, rpath):
        if not rpath:
            current_version = self.node.morphis_version

            latest_version_number = self.latest_version_number

            if latest_version_number\
                    and current_version != latest_version_number:
                version_str =\
                    '<span class="strikethrough nomargin">{}</span>]'\
                    '&nbsp;[<a href="{}{}">{} AVAILABLE</a>'\
                        .format(current_version,\
                            self.handler.maalstroom_url_prefix_str,\
                            "sp1nara3xhndtgswh7fznt414we4mi3y6kdwbkz4jmt8ocb6"\
                                "x4w1faqjotjkcrefta11swe3h53dt6oru3r13t667pr7"\
                                "cpe3ocxeuma/latest_version",\
                            latest_version_number)
            else:
                version_str = current_version

            content = templates.home_page_content[0].replace(\
                b"${CONNECTIONS}", str(self.connection_count).encode())
            content = content.replace(\
                b"${MORPHIS_VERSION}", version_str.encode())

            self._send_content([content, None])
            return

        s_upload = ".upload"
        s_dmail = ".dmail"
        s_aiwj = ".aiwj"

        if log.isEnabledFor(logging.DEBUG):
            log.debug("rpath=[{}].".format(rpath))

        if rpath.startswith(s_aiwj):
            self._send_content(\
                b"AIWJ - Asynchronous IFrames Without Javascript!")
            return
        elif rpath == "images/favicon.ico":
            self._send_content(\
                templates.favicon_content, content_type="image/png")
            return
        elif rpath.startswith(s_upload):
            if rpath.startswith(".upload/generate"):
                priv_key =\
                    base58.encode(\
                        rsakey.RsaKey.generate(bits=4096)._encode_key())

                self.send_response(307)
                self.send_header("Location", "{}".format(priv_key))
                self.send_header("Content-Length", 0)
                self.end_headers()
                return

            if len(rpath) == len(s_upload):
                self._send_content(static_upload_page_content)
            else:
                content =\
                    upload_page_content.replace(\
                        b"${PRIVATE_KEY}",\
                        rpath[len(s_upload)+1:].encode())
                content =\
                    content.replace(\
                        b"${VERSION}",\
                        str(int(time.time()*1000)).encode())
                content =\
                    content.replace(\
                        b"${UPDATEABLE_KEY_MODE_DISPLAY}",\
                        b"")
                content =\
                    content.replace(\
                        b"${STATIC_MODE_DISPLAY}",\
                        b"display: none")

                self._send_content(content)

            return
        elif rpath.startswith(s_dmail):
            if self.node.web_devel:
                importlib.reload(maalstroom.templates)
                importlib.reload(maalstroom.dmail)

            yield from maalstroom.dmail.serve_get(self, rpath)
            return

        if self.handler.headers["If-None-Match"] == rpath:
            cache_control = self.handler.headers["Cache-Control"]
            if cache_control != "max-age=0":
                self.send_response(304)
                if cache_control:
                    # This should only have been sent for an updateable key.
                    self.send_header("Cache-Control", "max-age=15, public")
                else:
                    self.send_header("ETag", rpath)
                self.send_header("Content-Length", 0)
                self.end_headers()
                return

        significant_bits = None

        # At this point we assume it is a key URL.

        if not self.connection_count:
            self._send_error("No connected nodes; cannot fetch from the"\
                " network.")
            return

        path_sep_idx = rpath.find('/')
        if path_sep_idx != -1:
            path = rpath[path_sep_idx+1:].encode()
            rpath = rpath[:path_sep_idx]
        else:
            path = None

        try:
            data_key, significant_bits = mutil.decode_key(rpath)
        except:
            self._send_error(\
                "Invalid encoded key: [{}].".format(rpath),\
                errcode=400)
            return

        data_rw = DataResponseWrapper()

        #TODO: YOU_ARE_HERE: Merge this in! This is why big downloads don't
        # start right away! It doesn't send headers till first data! But we
        # have version rigth away from root block, don't need to wait that
        # long. Etc.
        self.node.loop.call_soon_threadsafe(\
            asyncio.async,\
            _send_get_data(data_key, significant_bits, path, data_rw))

        data = data_rw.data_queue.get()

        if significant_bits:
            if data_rw.data_key:
                key = mbase32.encode(data_rw.data_key)

                if path:
                    url = "{}{}/{}"\
                        .format(\
                            self.handler.maalstroom_url_prefix_str,\
                            key,\
                            path.decode("UTF-8"))
                else:
                    url = "{}{}"\
                        .format(\
                            self.handler.maalstroom_url_prefix_str,\
                            key)

                message = "<html><head><title>Redirecting to Full Key</title>"\
                    "</head><body><a href=\"{}\">{}</a>\n{}</body></html>"\
                        .format(url, url, key).encode()

                self.send_response(301)
                self.send_header("Location", url)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", len(message))
                self.end_headers()

                self.write(message)
                self.finish_response()
                return

        if data:
            self.send_response(200)

            rewrite_url = False

            if data_rw.mime_type:
                self.send_header("Content-Type", data_rw.mime_type)
                if data_rw.mime_type\
                        in ("text/html", "text/css", "application/javascript"):
                    rewrite_url = True
            else:
                dh = data[:160]

                if dh[0] == 0xFF and dh[1] == 0xD8:
                    self.send_header("Content-Type", "image/jpg")
                elif dh[0] == 0x89 and dh[1:4] == b"PNG":
                    self.send_header("Content-Type", "image/png")
                elif dh[:5] == b"GIF89":
                    self.send_header("Content-Type", "image/gif")
                elif dh[:5] == b"/*CSS":
                    self.send_header("Content-Type", "text/css")
                    rewrite_url = True
                elif dh[:12] == b"/*JAVASCRIPT":
                    self.send_header("Content-Type", "application/javascript")
                    rewrite_url = True
                elif dh[:8] == bytes(\
                        [0x00, 0x00, 0x00, 0x18, 0x66, 0x74, 0x79, 0x70])\
                        or dh[:8] == bytes(\
                        [0x00, 0x00, 0x00, 0x1c, 0x66, 0x74, 0x79, 0x70]):
                    self.send_header("Content-Type", "video/mp4")
                elif dh[:8] == bytes(\
                        [0x50, 0x4b, 0x03, 0x04, 0x0a, 0x00, 0x00, 0x00]):
                    self.send_header("Content-Type", "application/zip")
                elif dh[:5] == bytes(\
                        [0x25, 0x50, 0x44, 0x46, 0x2d]):
                    self.send_header("Content-Type", "application/pdf")
                elif dh[:4] == b"RIFF" and dh[8:11] == b"AVI":
                    self.send_header("Content-Type", "video/avi")
                else:
                    dhl = dh.lower()

                    if (dhl.find(b"<html") > -1 or dhl.find(b"<HTML>") > -1)\
                            and (dhl.find(b"<head>") > -1\
                                or dhl.find(b"<HEAD") > -1):
                        self.send_header("Content-Type", "text/html")
                        rewrite_url = True
                    else:
                        self.send_header(\
                            "Content-Type", "application/octet-stream")

            rewrite_url = rewrite_url\
                and not self.handler.maalstroom_plugin_used

            if rewrite_url:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", data_rw.size)

            if data_rw.version is not None:
                self.send_header("Cache-Control", "max-age=15, public")
#                self.send_header("ETag", rpath)
            else:
                self.send_header("Cache-Control", "public")
                self.send_header("ETag", rpath)

            self.end_headers()

            try:
                while True:
                    if rewrite_url:
                        self._send_partial_content(data)
                    else:
                        self.write(data)

                    data = data_rw.data_queue.get()

                    if data is None:
                        if data_rw.timed_out:
                            log.warning(\
                                "Request timed out; closing connection.")
                            self.close_connection = True

                        if rewrite_url:
                            self._end_partial_content()
                        else:
                            self.finish_response()
                        break
            except ConnectionError:
                if log.isEnabledFor(logging.INFO):
                    log.info("Maalstroom request got broken pipe from HTTP"\
                        " side; cancelling.")
                data_rw.cancelled.set()

        else:
            self._handle_error(data_rw)

    def do_POST(self, rpath):
        if not self.connection_count:
            self._send_error("No connected nodes; cannot upload to the"\
                " network.")
            return

        log.info("POST; rpath=[{}].".format(rpath))

        if rpath != ".upload/upload":
            yield from maalstroom.dmail.serve_post(self, rpath)
            return

        if log.isEnabledFor(logging.DEBUG):
            log.info(self.handler.headers)

        if self.handler.headers["Content-Type"]\
                == "application/x-www-form-urlencoded":
            log.debug("Content-Type=[application/x-www-form-urlencoded].")
            data = self.rfile.read(int(self.handler.headers["Content-Length"]))
            privatekey = None
        else:
            if log.isEnabledFor(logging.DEBUG):
                log.debug("Content-Type=[{}]."\
                    .format(self.headers["Content-Type"]))

            form = cgi.FieldStorage(\
                fp=self.rfile,\
                headers=self.handler.headers,\
                environ={\
                    "REQUEST_METHOD": "POST",\
                    "CONTENT_TYPE": self.handler.headers["Content-Type"]})

            if log.isEnabledFor(logging.DEBUG):
                log.debug("form=[{}].".format(form))

            formelement = form["fileToUpload"]
            filename = formelement.filename
            data = formelement.file.read()

            if log.isEnabledFor(logging.INFO):
                log.info("filename=[{}].".format(filename))

            try:
                privatekey = form["privateKey"].value

                if privatekey == "${PRIVATE_KEY}":
                    raise KeyError()

                if log.isEnabledFor(logging.INFO):
                    log.info("privatekey=[{}].".format(privatekey))

                privatekey = base58.decode(privatekey)

                privatekey = rsakey.RsaKey(privdata=privatekey)

                path = form["path"].value.encode()
                version = form["version"].value
                if not version:
                    version = 0
                else:
                    version = int(version)
                mime_type = form["mime_type"].value
            except KeyError:
                privatekey = None

        if log.isEnabledFor(logging.DEBUG):
            log.debug("data=[{}].".format(data))

        data_rw = DataResponseWrapper()

        if privatekey:
            node.loop.call_soon_threadsafe(\
                asyncio.async, _send_store_data(\
                    data, data_rw, privatekey, path, version, mime_type))
        else:
            node.loop.call_soon_threadsafe(\
                asyncio.async, _send_store_data(data, data_rw))

        data_rw.is_done.wait()

        if data_rw.data_key:
            enckey = mbase32.encode(data_rw.data_key)
            if privatekey and path:
                url = "{}{}/{}"\
                    .format(\
                        self.handler.maalstroom_url_prefix_str,\
                        enckey,\
                        path.decode("UTF-8"))
            else:
                url = "{}{}"\
                    .format(\
                        self.handler.maalstroom_url_prefix_str,\
                        enckey)

            if privatekey:
                message = '<a id="key" href="{}">updateable key link</a>'\
                    .format(url)

                if data_rw.referred_key:
                    message +=\
                        '<br/><a id="referred_key" href="{}{}">perma link</a>'\
                            .format(\
                                self.handler.maalstroom_url_prefix_str,\
                                mbase32.encode(data_rw.referred_key))
            else:
                message = '<a id="key" href="{}">perma link</a>'.format(url)

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", len(message))
            self.end_headers()

            self.write(bytes(message, "UTF-8"))
            self.finish_response()
        else:
            self._handle_error(data_rw)

    def get_accept_charset(self):
        if self._accept_charset:
            return self._accept_charset

        acharset = self.handler.headers["Accept-Charset"]
        if acharset:
            if acharset.find("ISO-8859-1") > -1\
                    and acharset.find("UTF-8") == -1:
                acharset = "ISO-8859-1"
            else:
                acharset = "UTF-8"
        else:
            acharset = "UTF-8"

        self._accept_charset = acharset
        return acharset

    def _send_204(self):
        self.send_response(204)
        self.send_header("Content-Length", 0)
        self.end_headers()
        return

    def _send_content(self, content_entry, cacheable=True, content_type=None):
        if type(content_entry) in (list, tuple):
            content = content_entry[0]
            content_id = content_entry[1]
        else:
            content = content_entry
            cacheable = False

        if not self.handler.maalstroom_plugin_used:
            content =\
                content.replace(\
                    b"morphis://", self.handler.maalstroom_url_prefix)

        if cacheable and not content_id:
            if callable(content):
                content = content()
            content_id = mbase32.encode(enc.generate_ID(content))
            content_entry[1] = content_id

        if cacheable and self.handler.headers["If-None-Match"] == content_id:
            cache_control = self.handler.headers["Cache-Control"]
            if cache_control != "max-age=0":
                self.send_response(304)
                if cache_control:
                    # This should only have been sent for an updateable key.
                    self.send_header("Cache-Control", "max-age=15, public")
                else:
                    self.send_header("Cache-Control", "public")
                    self.send_header("ETag", content_id)
                self.send_header("Content-Length", 0)
                self.end_headers()
                self.finish_response()
                return

        if callable(content):
            content = content()

        self.send_response(200)
        self.send_header("Content-Length", len(content))
        self.send_header("Content-Type",\
            "text/html" if content_type is None else content_type)
        if cacheable:
            self.send_header("Cache-Control", "public")
            self.send_header("ETag", content_id)
        else:
            self._send_no_cache()

        self.end_headers()
        self.write(content)
        self.finish_response()
        return

    def _send_partial_content(self, content, start=False, content_type=None,\
            cacheable=False):
        if type(content) is str:
            content = content.encode()

        if not self.handler.maalstroom_plugin_used:
            content =\
                content.replace(\
                    b"morphis://", self.handler.maalstroom_url_prefix)

        if start:
            self.send_response(200)
            if not cacheable:
                self._send_no_cache()
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Type",\
                "text/html" if content_type is None else content_type)
            self.end_headers()

        chunklen = len(content)

#        if not content.endswith(b"\r\n"):
#            add_line = True
#            chunklen += 2
#        else:
#            add_line = False

        self.write("{:x}\r\n".format(chunklen).encode())
        self.write(content)
#        if add_line:
#            self.write(b"\r\n")
        self.write(b"\r\n")

        self.flush()

    def _end_partial_content(self):
        self.write(b"0\r\n\r\n")
        self.finish_response()

    def _send_no_cache(self):
        self.send_header("Cache-Control",\
            "no-cache, no-store, must-revalidate")
#            "private, no-store, max-age=0, no-cache, must-revalidate, post-check=0, pre-check=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
#        self.send_header("Expires", "Mon, 26 Jul 1997 00:00:00 GMT")
#        self.send_header("Last-Modified", "Sun, 2 Aug 2015 00:00:00 GMT")

    def send_exception(self, exception, errcode=500):
        self._send_error(\
            errmsg=str("{}: {}".format(type(exception).__name__, exception)),\
            errcode=errcode)

    def _send_error(self, errmsg, errcode=500):
        if errcode == 400:
            errmsg = "400 Bad Request.\n\n{}"\
                .format(errmsg).encode()
            self.send_response(400)
        else:
            errmsg = "500 Internal Server Error.\n\n{}"\
                .format(errmsg).encode()
            self.send_response(500)

        self.send_header("Content-Length", len(errmsg))
        self.end_headers()
        try:
            self.write(errmsg)
            self.finish_response()
        except ConnectionError:
            log.info("HTTP client aborted request connection.")
            return

    def _handle_error(self, data_rw=None, errmsg=None, errcode=None):
        if not data_rw:
            errmsg = b"400 Bad Request."
            self.send_response(400)
        elif data_rw.exception:
            errmsg = b"500 Internal Server Error."
            self.send_response(500)
        elif data_rw.timed_out:
            errmsg = b"408 Request Timeout."
            self.send_response(408)
        else:
            errmsg = b"404 Not Found."
            self.send_response(404)

        self.send_header("Content-Length", len(errmsg))
        self.end_headers()
        try:
            self.write(errmsg)
            self.finish_response()
        except ConnectionError:
            log.info("HTTP client aborted request connection.")
            return

@asyncio.coroutine
def _send_get_data(data_key, significant_bits, path, data_rw):
    if log.isEnabledFor(logging.DEBUG):
        log.debug(\
            "Sending GetData: key=[{}], significant_bits=[{}], path=[{}]."\
                .format(mbase32.encode(data_key), significant_bits, path))

    try:
        if significant_bits:
            future = asyncio.async(\
                node.chord_engine.tasks.send_find_key(\
                    data_key, significant_bits),
                loop=node.loop)

            yield from asyncio.wait_for(future, 15.0, loop=node.loop)

            ct_data_rw = future.result()

            data_key = ct_data_rw.data_key

            if not data_key:
                data_rw.data = b"Key Not Found"
                data_rw.version = -1
                data_rw.data_queue.put(None)
                return

            if log.isEnabledFor(logging.INFO):
                log.info("Found key=[{}].".format(mbase32.encode(data_key)))

            data_rw.data_key = bytes(data_key)
            data_rw.data_queue.put(None)
            return

#        future = asyncio.async(\
#            node.chord_engine.tasks.send_get_data(data_key),\
#            loop=node.loop)
#
#        yield from asyncio.wait_for(future, 15.0, loop=node.loop)
#
#        ct_data_rw = future.result()

        data_callback = Downloader(data_rw)

        r = yield from multipart.get_data(\
                node.chord_engine, data_key, data_callback, path=path,
                ordered=True)

        if r is False:
            raise asyncio.TimeoutError()
    except asyncio.TimeoutError:
        data_rw.timed_out = True
    except:
        log.exception("send_get_data(..)")
        data_rw.exception = True

    data_rw.data_queue.put(None)

class KeyCallback(multipart.KeyCallback):
    def __init__(self, data_rw):
        self.data_rw = data_rw

    def notify_key(self, key):
        self.data_rw.data_key = key

    def notify_referred_key(self, key):
        self.data_rw.referred_key = key

@asyncio.coroutine
def _send_store_data(data, data_rw, privatekey=None, path=None, version=None,\
        mime_type=""):

    try:
        key_callback = KeyCallback(data_rw)

        yield from multipart.store_data(\
            node.chord_engine, data, privatekey=privatekey, path=path,\
            version=version, key_callback=key_callback, mime_type=mime_type)
    except asyncio.TimeoutError:
        data_rw.timed_out = True
    except:
        log.exception("send_store_data(..)")
        data_rw.exception = True

    data_rw.is_done.set()

class Downloader(multipart.DataCallback):
    def __init__(self, data_rw):
        super().__init__()

        self.data_rw = data_rw

    def notify_version(self, version):
        self.data_rw.version = version

    def notify_size(self, size):
        if log.isEnabledFor(logging.INFO):
            log.info("Download size=[{}].".format(size))
        self.data_rw.size = size

    def notify_mime_type(self, val):
        if log.isEnabledFor(logging.INFO):
            log.info("mime_type=[{}].".format(val))
        self.data_rw.mime_type = val

    def notify_data(self, position, data):
        if self.data_rw.cancelled.is_set():
            return False

        self.data_rw.data_queue.put(data)

        return True

class DataResponseWrapper(object):
    def __init__(self):
        self.data = None
        self.size = None

        self.data_key = None
        self.referred_key = None # If data_key is a link on upload.

        self.path = None
        self.version = None
        self.mime_type = None

        self.is_done = Event()
        self.data_queue = queue.Queue()

        self.exception = None
        self.timed_out = False

        self.cancelled = Event()

