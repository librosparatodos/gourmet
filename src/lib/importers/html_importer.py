import htmllib, urllib, re, StringIO, tempfile, os, os.path
import importer
import BeautifulSoup
import socket
from html_plugins import *
from gourmet.gdebug import *
from gettext import gettext as _

DEFAULT_SOCKET_TIMEOUT=45.0
URLOPEN_SOCKET_TIMEOUT=15.0

socket.setdefaulttimeout(DEFAULT_SOCKET_TIMEOUT)
# To add additional HTML import sites, see html_rules.py

def read_socket_w_progress (socket, progress, message):
    """Read piecemeal reporting progress as we go."""
    if not progress: data = socket.read()
    else:
        bs = 1024 * 8
        if hasattr(socket,'headers'):
            fs = int(socket.headers.get('content-length',-1))
        else: fs = -1
        block = socket.read(bs)
        data = block
        sofar = bs
        while block:
            if fs>0: progress(float(sofar)/fs, message)
            else: progress(-1, message)
            sofar += bs
            block = socket.read(bs)
            data += block
    socket.close()
    return data

def get_url (url, progress):
    """Return data from URL, possibly displaying progress."""
    if type(url)==str:
        socket.setdefaulttimeout(URLOPEN_SOCKET_TIMEOUT)
        sock = urllib.urlopen(url)
        socket.setdefaulttimeout(DEFAULT_SOCKET_TIMEOUT)
        return read_socket_w_progress(sock,progress,_('Retrieving %s'%url))
    else:
        sock = url
        return read_socket_w_progress(sock,progress,_('Retrieving file'))

class BeautifulSoupScraper:
    """We take a set of rules and create a scraper using BeautifulSoup.
    
    This will be quite wonderfully magical. Handed rules, we can
    customize a scraper for any set of data from any website.

    Writing new rules should be simpler than writing a new class would
    be. The rules will take the following form:
    ['foobar',DIRECTIONS_TO_TAG,METHOD_OF_STORAGE, POST_PROCESSING]

    DIRECTIONS_TO_TAG is a list of instructions followed to find our
    tag. We can search by tagname and attributes or by text. By
    default, we drill down the structure each time.
    
    METHOD_OF_STORAGE is either TEXT or MARKUP, depending what we want
    to store in our return dictionary.

    OPTIONAL POST_PROCESSING, which can be a function or a regexp.  If
    it is a regexp, it should have a grouping construct which will
    contain the text we want to keep.
    """
    TEXT = 'text'
    MARKUP = 'markup'
    def __init__ (self, rules):
        """Set up a scraper according to a list of rules."""
        self.rules = rules

    def feed_url (self, url,progress=None):
        """Feed ourselves a url.

        URL can be a string or an already open socket.
        """
        self.feed_data(get_url(url,progress))

    def feed_data (self, data):
        self.soup = BeautifulSoup.BeautifulSoup(data)

    def scrape_url (self, url, progress=None):
        self.feed_url(url,progress)
        return self.scrape()

    def scrape_data (self, data):
        self.feed_data(data)
        return self.scrape()

    def scrape (self):
        """Do our actual scraping according to our rules."""
        self.dic = {}
        for rule in self.rules:
            self.apply_rule(rule)
        return self.dic

    def apply_rule (self, rule):
        """Apply a rule from our rule list."""
        if len(rule)==3:
            store_as,tagpath,retmethod = rule
            post_processing=None
        elif len(rule)==4:
            store_as,tagpath,retmethod,post_processing=rule
        tag = self.get_tag_according_to_path(tagpath)
        self.store_tag(store_as,tag,retmethod,post_processing)

    def post_process (self, post_processing, value, tag):
        """Post process value according to post_processing

        post_processing is either callable (and will return a modified
        string based on what it's handed), or a tuple: (regexp,
        force_match).

        The regexp must always yield the desired value in the first
        grouping construct (if you require something more complicated,
        write a lambda).

        If force_match is True, return '' if there is no
        match. Otherwise, default to the unadulterated value.
        """
        if type(post_processing) == tuple and len(post_processing)==2:
            regexp=re.compile(post_processing[0],re.UNICODE)
            m=regexp.search(value)
            if m: return m.groups()[0]
            else:
                if post_processing[1]: return ""
                else: return value
        elif callable(post_processing):
            return post_processing(value,tag)
        else:
            return value
            
    def get_tag_according_to_path (self, path):
        """Follow path to tag.

        Path is a list of instructions.
        """
        base = self.soup
        for step in path:
            base=self.follow_path(base,step)
            if type(base)==list:
                # then we'd better be the last step
                break
        return base

    def follow_path (self, base, step):
        """Follow step from base of base.

        Base is a tag. Step is a set of instructions as a dictionary.

        {'regexp':regexp}
        {'string':string}
        OR
        {'tag':tagname,
         'attributes':{attr:name,attr:name,...},
         'index': NUMBER or [FIRST,LAST],
         }
        """
        if not base: return # path ran out...
        ind=step.get('index',0)
        if step.has_key('regexp'):
            ret = base.fetchText(re.compile(step['regexp']))
        elif step.has_key('string'):
            ret = base.fetchText('string')        
        else:
            get_to = None
            if ind:
                if type(ind)==list: get_to=ind[-1]
                elif type(ind)==int: get_to=ind
                if not get_to or get_to < 0: get_to=None
                else: get_to += 1
            if get_to:
                ret = base.fetch(step.get('tag'),
                                 step.get('attributes'),
                                 get_to)
            else:
                ret = base.fetch(step.get('tag'),step.get('attributes'))
        if ret:
            # if we have moveto, we do it with our index -- for
            # example, if we have step['moveto']='parent', we grab the
            # parents of each tag we would otherwise return. This can
            # also work for previousSibling, nextSibling, etc.
            if step.has_key('moveto'):
                ret = [getattr(o,step['moveto']) for o in ret]
            if type(ind)==list:                
                return ret[ind[0]:ind[1]]
            else: #ind is an integer
                if ind < len(ret):
                    return ret[ind]
                else:
                    print 'Problem following path.'
                    print 'I am supposed to get item: ',ind
                    print 'from: ',ret
                    print 'instructions were : ',
                    try: print 'base: ',base
                    except UnicodeDecodeError: print '(ugly unicodeness)'
                    try: print 'step: ',step
                    except UnicodeDecodeError: print '(ugly unicodeness)'

    def store_tag (self, name, tag, method, post_processing=None):
        """Store our tag in our dictionary according to our method."""
        if type(tag)==list:
            for t in tag: self.store_tag(name,t,method,post_processing)
            return
        if method==self.TEXT:
            if tag: val = get_text(tag)
            else: val = ""
        elif method==self.MARKUP: 
            if tag: val = tag.prettify()
            else: val = ""
        else: #otherwise, we assume our method is an attribute name
            val = ""
            if tag:
                for aname,aval in tag.attrs:
                    if aname==method: val=aval
        if post_processing:
            val=self.post_process(post_processing, val, tag)
        if not val: return # don't store empty values
        if self.dic.has_key(name):
            curval = self.dic[name]
            if type(curval)==list: self.dic[name].append(val)
            else: self.dic[name]=[self.dic[name],val]
        else:
            self.dic[name]=val

class GenericScraper (BeautifulSoupScraper):
    """A very simple scraper.

    We grab a list of images and all the text.
    """
    def __init__ (self):
        BeautifulSoupScraper.__init__(self,
            [['text',
              [{'tag':'body'}],
              'text',
              ],
             ['images',
              [{'tag':'img',
                'index':(0,None)}],
              'src',
              lambda s,v: [s]],
             ['title',
              [{'tag':'title'}],
              'text',],
             ]
            )

    def scrape (self):
        dic = BeautifulSoupScraper.scrape(self)
        print 'scraped...',dic
        text = dic['title']+'\n'+dic['text']
        images = dic['images']
        print images
        #images = [(isinstance(i,str) and i or
        #           i[0]) for i in dic['images']]
        return text,images
        
def get_text (tag, strip=True):
    """Get text from tag.

    We are willing to look at children and ignore them.
    We will get rid of white space and prevent <BR> tags with newlines.
    """
    if tag.string:
        if strip:
            ret=tag.string
            ret = re.sub('\s+',' ',ret)
            ret = ret.strip()
            return unicode(ret)
        else: return unicode(tag.string)
    else:
        stuff = tag.prettify()
        stuff = re.sub('\s+',' ',stuff)
        stuff = re.sub('<[bB][rR]\s*/?>','\n',stuff)
        stuff = re.sub('<[^>]+>','',stuff)
        return unicode(stuff.strip())

img_src_regexp = re.compile('<img[^>]+src=[\'\"]([^\'"]+)')

def get_image_from_tag (iurl, page_url):
    if not iurl: return
    if iurl[0]=="/":
        base_url = "/".join(page_url.split('/')[0:3]) + '/'
    else:
        base_url = "/".join(page_url.split('/')[0:-1]) + '/'
    iurl = base_url + iurl
    tmpfi,info=urllib.urlretrieve(iurl)
    ifi=file(tmpfi,'rb')
    retval=ifi.read()
    ifi.close()
    return retval

def scrape_url (url, progress=None):
    if type(url)==str: domain=url.split('/')[2]
    if SUPPORTED_URLS.has_key(domain):
        bss = BeautifulSoupScraper(SUPPORTED_URLS[domain])
    else:
        bss = None
        for regexp,v in SUPPORTED_URLS_REGEXPS.items():
            if re.match(regexp,domain):
                bss=BeautifulSoupScraper(v)
                break
    if bss:
        return bss.scrape_url(url,progress=progress)

def add_to_fn (fn):
    f,e=os.path.splitext(fn)
    try:
        f,n=os.path.splitext(f)
        n = int(n[1:])
        n += 1
        return f + "%s%s"%(os.path.extsep,n) + e
    except:
        return f + "%s1"%os.path.extsep + e
    
def import_url (url, rd, progress=None, add_webpage_source=True, threaded=False):
    """Import information from URL.
    We handle HTML with scrape_url.

    Everything else, we hand back to our caller as a list of
    files. This is a little stupid -- it would be more elegant to just
    hand back a class, but our importer stuff is a little munged up
    with gui-ness and it's just going to have to be ugly for now
    """
    if progress: progress(0.01,'Fetching webpage')
    sock=urllib.urlopen(url)
    header=sock.headers.get('content-type','text/html')
    if progress: progress(0.02, 'Reading headers')
    if header.find('html')>=0:
        #return scrape_url(url,progress)
        return WebPageImporter(rd,
                               url,
                               prog=progress,
                               add_webpage_source=add_webpage_source,
                               threaded=threaded)
    elif header=='application/zip':
        import zip_importer
        return zip_importer.zipfile_to_filelist(sock,progress,os.path.splitext(url.split('/')[-1])[0])
    else:
        fn = os.path.join(tempfile.tempdir,url.split('/')[-1])
        while os.path.exists(fn):
            fn=add_to_fn(fn)
        ofi = open(fn,'w')
        ofi.write(get_url(sock,progress))
        ofi.close()
        return [fn]

class WebPageImporter (importer.importer):
    """Import a webpage as a recipe

    We use our BeautifulSoupScraper class to do the actual scraping.
    We use predefined webpages already registered in the global variable
    SUPPORTED_URLS in this module. If we don't know the web page, we will raise
    an error.

    To create a new type of web page import, create a new set of
    import rules and register them with SUPPORTED_URLS.
    """

    JOIN_AS_PARAGRAPHS = ['instructions','modifications','ingredient_block']

    def __init__ (self, rd, url, add_webpage_source=True,
                  threaded=False, total=0, prog=None,conv=None):
        self.add_webpage_source=add_webpage_source
        self.url = url
        self.prog = prog
        importer.importer.__init__(self,rd,threaded=threaded,total=total,prog=prog,do_markup=False,
                                   conv=conv)

    def run (self):
        """Import our recipe to our database.

        This must be called after self.d is already populated by scraping
        our web page.
        """
        debug('Scraping url %s'%self.url,0)
        self.d = scrape_url(self.url, progress=self.prog)
        debug('Scraping url returned %s'%self.d,0)
        if not self.d:
            # Interactive we go...
            gs = GenericScraper()
            text,images = gs.scrape_url(self.url, progress=self.prog)
            import interactive_importer
            ii = interactive_importer.InteractiveImporter()
            ii.set_text(text)
            ii.run()
            return

        self.start_rec()
        # Add webpage as source
        if self.add_webpage_source:
            # add Domain as source
            domain = self.url.split('/')[2]
            src=self.d.get('source',None)
            add_str = '(%s)'%domain
            if type(src)==list: src.append(add_str)
            elif src: src = [src,add_str]
            else: src = domain # no parens if we're the only source
            self.d['source']=src
            # and add a note to instructions with the full URL
            instr=self.d.get('instructions',[])
            add_str=_('Retrieved from %(url)s.')%{'url':self.url}
            if type(instr)==list: instr.append(add_str)
            else: instr = [instr,add_str]
            self.d['instructions']=instr
        for k,v in self.d.items():
            debug('processing %s:%s'%(k,v),1)
            if self.prog: self.prog(-1,_('Importing recipe'))
            if k=='ingredient_parsed':
                if type(v) != list: v=[v]
                for ingdic in v:
                    if self.prog: self.prog(-1,_('Processing ingredients'))
                    # we take a special keyword, "text", which gets
                    # parsed
                    if ingdic.has_key('text'):
                        d = self.rd.ingredient_parser(ingdic['text'])
                        if d:
                            for dk,dv in d.items(): ingdic[dk]=dv
                        del ingdic['text']
                    self.start_ing(**ingdic)
                    self.commit_ing()
            elif type(v)==list:
                if k in self.JOIN_AS_PARAGRAPHS: v = "\n".join(v)
                else: v = " ".join(v)
            if k == 'ingredient_block':
                for l in v.split('\n'):
                    if self.prog: self.prog(-1,_('Processing ingredients.'))
                    dic=self.rd.ingredient_parser(l)
                    if dic:
                        self.start_ing(**dic)
                    self.commit_ing()
            elif k == 'image':
                try:
                    if v: self.rec['image']=get_image_from_tag(v,self.url)
                except:
                    debug('Error retrieving image',0)
                    debug('tried to retrieve image from %s'%v,0)
                    raise
            else: self.rec[k]=v
        self.commit_rec()
        if self.prog: self.prog(1,_('Import complete.'))