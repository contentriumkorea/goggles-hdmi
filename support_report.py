"""Bounded, local, allowlisted troubleshooting snapshot. No raw logs or secrets."""
import datetime
import json
import math
import re
import platform as host_platform
import sys
import time

MESSAGES = {
    'GH-USB-MISSING':'고글 USB 장치를 찾지 못했습니다. 데이터 케이블과 OTG 설정을 확인하세요.',
    'GH-USB-DEPENDENCY':'USB 수신 라이브러리를 찾지 못했습니다. 배포 패키지를 다시 설치하세요.',
    'GH-USB-ACCESS':'USB 접근이 거부되었습니다. 장치 사용 앱과 접근 권한을 확인하세요.',
    'GH-USB-BUSY':'USB 또는 수신 주소가 사용 중입니다. 다른 고글 수신 앱을 종료하세요.',
    'GH-USB-DESCRIPTOR':'지원하지 않거나 불명확한 USB 인터페이스입니다. 문제 정보를 복사하세요.',
    'GH-USB-DISCONNECTED':'USB 장치 연결이 끊겼습니다. 다시 연결하세요.',
    'GH-USB-CONFIG':'고글 USB 주소 설정이 필요합니다. USB 최초 설정을 실행하세요.',
    'GH-RNDIS-INIT':'고글 RNDIS 초기화 응답을 확인하지 못했습니다. USB를 다시 연결하세요.',
    'GH-RNDIS-FRAMING':'USB 패킷의 길이 또는 구조가 올바르지 않습니다. 문제 정보를 복사하세요.',
    'GH-RNDIS-RECOVERED':'별도 USB 읽기 사이의 비정상 잔여 1바이트를 검증 후 복구했습니다.',
    'GH-ARP-TIMEOUT':'고글의 USB 네트워크 응답이 없습니다. OTG 유선 컴퓨터 연결을 확인하세요.',
    'GH-TRANSPORT-IO':'USB 영상 통신 실패 · 다시 연결 중입니다.',
    'GH-VIDEO-TIMEOUT':'영상 패킷 대기 시간이 초과되었습니다. 라이브뷰 공유와 기체 영상을 확인하세요.',
    'GH-VIDEO-GAP':'영상 패킷 누락 또는 수신 대기열 초과 · 다시 연결 중입니다.',
    'GH-DECODE':'사용 가능한 영상 프레임을 복원하지 못했습니다. 다시 연결 중입니다.',
    'GH-OUTPUT':'출력 화면을 열지 못했습니다. 화면 연결과 단축키 상태를 확인하세요.',
    'GH-UPDATE-VERIFY':'업데이트 서명 또는 파일 검증에 실패했습니다.',
    'GH-UPDATE-IO':'업데이트 다운로드 또는 설치 프로그램 실행에 실패했습니다.',
    'GH-UNKNOWN':'연결 작업에 실패했습니다. 문제 정보를 복사하세요.'}
STAGES = {'idle','discovery','network_config','socket_bind','claim','rndis_init','arp','session','video','decode','output','update'}
COUNTERS = ('sessions','retries','invalid_packets','video_bytes','ordered_bytes','parsed_frames','frames',
            'warmup_frames','intra_refresh_frames','decode_errors','width','height','session_attempts',
            'usb_read_calls','usb_read_bytes','last_usb_read_bytes','rndis_messages','rndis_partial_reads',
            'rndis_buffered_bytes','rndis_expected_bytes','rndis_max_buffered_bytes','rndis_framing_errors',
            'rndis_control_ms','rndis_max_control_ms','max_ack_gap_ms','usb_timeout_reads',
            'usb_empty_reads','rndis_zero_padding_bytes','rndis_boundary_recoveries','rndis_discarded_boundary_bytes')
CONTEXT_LIMITS = {'buffered_bytes':2*1024*1024,'expected_bytes':2*1024*1024,
    'header_type':2**32-1,'header_length':2**32-1,'pending_prefix_bytes':3,
    'pending_prefix_value':2**24-1,'read_bytes':1024*1024,'previous_read_bytes':1024*1024,
    'usb_read_call':2**63,'validated_messages':65535}


def safe_context(context):
    return {key:value for key,limit in CONTEXT_LIMITS.items()
            if type(value := context.get(key)) is int and 0<=value<=limit}
EXCEPTION_CLASSES = {'OSError','ValueError','ConnectionError','TimeoutError','USBError','USBTimeoutError',
                     'InvalidDataError','FFmpegError','RNDISFramingError','SupportError','OtherError'}
REASONS = {'partial_timeout','buffer_limit','message_type','length_limit','data_bounds','metadata','one_byte_boundary'}
OPERATIONS = {'usb_read','usb_write','rndis_parse','network_receive','rndis_keepalive','rndis_initialize',
              'rndis_query_mac','rndis_query_mtu','rndis_set_filter','arp_send','session_send','ack_send',
              'video_receive','decode_create','decode_parse','decode_frame','decode_recovery'}
DISPLAY_REASONS={'ok','screen_unmatched','screen_ambiguous','mode_unavailable','native_query_failed'}
OUTPUT_EVENTS={'show','hide','activate','deactivate','window_state','repair_skipped','fullscreen_repair',
               'topology','window_screen','manual_restore','open','release','screen_added','screen_removed','escape'}


def safe_output(value):
    if not isinstance(value,dict):return {}
    result={key:number for key in ('width','height','refresh_hz','dpr','screen_count','window_state','fullscreen_repairs')
            if type(number:=value.get(key)) in (int,float) and math.isfinite(number) and 0<=number<=100000}
    for key in ('locked','visible','fullscreen','minimized','pending','native_fullscreen','native_fullscreen_style','native_visible','native_minimized'):
        if type(value.get(key)) is bool:result[key]=value[key]
    for key in ('index','requested_screen','actual_screen'):
        if type(number:=value.get(key)) is int and -1<=number<=31:result[key]=number
    if isinstance(value.get('mode_reason'),str) and value['mode_reason'] in DISPLAY_REASONS:result['mode_reason']=value['mode_reason']
    if isinstance(value.get('release_reason'),str) and value['release_reason'] in ('not_started','user_release','screen_removed'):
        result['release_reason']=value['release_reason']
    geometry=value.get('geometry')
    if isinstance(geometry,list) and len(geometry)==4 and all(type(n) is int and -100000<=n<=100000 for n in geometry):
        result['geometry']=geometry[:]
    events=value.get('events')
    if isinstance(events,list):
        result['events']=[dict(safe_output({k:v for k,v in event.items() if k!='events' and k!='screens'}),event=event['event'])
            for event in events[-10:] if isinstance(event,dict) and isinstance(event.get('event'),str) and event['event'] in OUTPUT_EVENTS]
    screens=value.get('screens')
    if isinstance(screens,list):result['screens']=[safe_output({k:v for k,v in s.items() if k!='screens' and k!='events'})
        for s in screens[:16] if isinstance(s,dict)]
    return result


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


class SupportError(OSError):
    def __init__(self, code, stage, original=None, *, operation=None):
        self.code = code if code in MESSAGES else 'GH-UNKNOWN'
        self.stage = stage if stage in STAGES else 'discovery'
        self.os_error = next((value for value in (getattr(original,'winerror',None),getattr(original,'errno',None),
            getattr(original,'backend_error_code',None)) if type(value) is int and -65535 <= value <= 65535),None)
        name = getattr(original,'exception_class',None) or (type(original).__name__ if original is not None else None)
        self.exception_class = name if name in EXCEPTION_CLASSES else ('OtherError' if name else None)
        self.reason = getattr(original,'reason',None) if getattr(original,'reason',None) in REASONS else None
        self.operation = operation if operation in OPERATIONS else None
        context = getattr(original,'context',{})
        context = dict(context) if isinstance(context,dict) else {}
        context.update({key:getattr(original,key,None) for key in ('buffered_bytes','expected_bytes')})
        self.context = safe_context(context)
        super().__init__(MESSAGES[self.code])


def issue_from_exception(exc, stage, *, operation=None):
    if isinstance(exc,SupportError):
        if exc.operation is None and operation in OPERATIONS:exc.operation = operation
        return exc
    if type(exc).__name__ == 'RNDISFramingError':
        code = 'GH-RNDIS-FRAMING'
    elif getattr(exc,'winerror',None) == 10048 or getattr(exc,'errno',None) in (16,48,98) or getattr(exc,'backend_error_code',None) == -6:
        code = 'GH-USB-BUSY'
    elif getattr(exc,'errno',None) in (1,13) or getattr(exc,'backend_error_code',None) == -3:
        code = 'GH-USB-ACCESS'
    elif getattr(exc,'errno',None) == 19 or getattr(exc,'backend_error_code',None) == -4:
        code = 'GH-USB-DISCONNECTED'
    elif stage == 'rndis_init':
        code = 'GH-RNDIS-INIT'
    elif stage == 'decode':
        code = 'GH-DECODE'
    elif isinstance(exc,OSError):
        code = 'GH-TRANSPORT-IO'
    else:
        code = 'GH-UNKNOWN'
    return SupportError(code,stage,exc,operation=operation)


def issue_record(code,stage,exc=None):
    issue = exc if isinstance(exc,SupportError) else SupportError(code,stage,exc)
    result = {'time_utc':utc_now(),'code':issue.code,'stage':issue.stage,'message':MESSAGES[issue.code],
              'os_error':issue.os_error,'repeat_count':1}
    for key in ('exception_class','reason','operation','context'):
        value = getattr(issue,key,None)
        if value:result[key] = value
    return result


def record_issue(stats, code, stage, exc=None, *, active=True):
    issue = issue_record(code,stage,exc)
    history = stats.setdefault('issue_history',[])
    attempt = stats.get('session_attempts')
    if type(attempt) is int and 0 <= attempt <= 2**63:issue['session_attempt'] = attempt
    if history and all(history[-1].get(key) == issue.get(key) for key in
            ('time_utc','code','stage','os_error','exception_class','reason','operation','session_attempt','context')):
        history[-1]['repeat_count'] = min(1000000,history[-1].get('repeat_count',1)+1)
        issue = history[-1]
    else:
        history.append(issue)
    if isinstance(history,list):
        del history[:-10]
    if active:
        stats['active_issue'] = issue
    stats['stage'] = stage
    return issue


def record_success(stats,stage, *, recovered=True):
    stats['stage'] = stage
    stats['last_successful_stage'] = stage
    if recovered:
        stats['active_issue'] = None


def safe_issue(value):
    if not isinstance(value,dict) or value.get('code') not in MESSAGES:
        return None
    result = {'code':value['code'],'stage':value.get('stage') if value.get('stage') in STAGES else 'idle',
              'message':MESSAGES[value['code']]}
    stamp = value.get('time_utc')
    if isinstance(stamp,str) and len(stamp) <= 40:
        try:
            result['time_utc'] = datetime.datetime.fromisoformat(stamp).isoformat(timespec='seconds')
        except ValueError:
            pass
    error = value.get('os_error')
    if type(error) is int and -65535 <= error <= 65535:
        result['os_error'] = error
    for key,allowed in (('exception_class',EXCEPTION_CLASSES),('reason',REASONS),('operation',OPERATIONS)):
        if value.get(key) in allowed:result[key] = value[key]
    for key in ('repeat_count','session_attempt'):
        number = value.get(key)
        if type(number) is int and 0 <= number <= 2**63:result[key] = number
    context = value.get('context')
    if isinstance(context,dict):
        result['context'] = safe_context(context)
    return result


def build_report(*, version, build_revision=None, platform=None, os_version=None, architecture=None, mode='idle',stats=None,
                 diagnosis=None,components=None,output=None,last_frame=0,logs=None):
    stats = stats or {}
    counters = {key:value for key in COUNTERS if type(value := stats.get(key)) in (int,float) and
                math.isfinite(value) and 0 <= value <= 2**63}
    stages = {key:stats.get(key) if stats.get(key) in STAGES else 'not_started' for key in ('stage','last_successful_stage')}
    stages['stage'] = 'idle' if mode == 'idle' else stages['stage']
    selected_platform = platform or sys.platform
    selected_os = os_version or (host_platform.mac_ver()[0] if selected_platform == 'darwin' else host_platform.version())
    selected_arch = architecture or host_platform.machine()
    report = {'schema_version':1,'captured_utc':utc_now(),'app_version':version if isinstance(version,str) and re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}',version) else 'unknown',
        'build':build_revision if isinstance(build_revision,str) and re.fullmatch('[0-9a-f]{40}',build_revision) else 'unknown',
        'platform':selected_platform if selected_platform in ('darwin','win32') else 'unknown',
        'os_version':selected_os if isinstance(selected_os,str) and re.fullmatch(r'[0-9.]{1,40}',selected_os) else 'unknown',
        'architecture':selected_arch if selected_arch in ('arm64','aarch64','x86_64','AMD64','x86','i386') else 'unknown',
        'source_mode':mode if mode in ('idle','usb','stream','pattern') else 'unknown',
        **stages,'active_issue':safe_issue(stats.get('active_issue')),'counters':counters,
        'recent_issues':[safe for item in list(stats.get('issue_history',[]))[-10:] if (safe := safe_issue(item))],
        'components':{key:safe for key,item in (components or {}).items() if key in ('output','update') and (safe := safe_issue(item))},
        'device_detail':'not_collected','protocol':{'vid':'2CA3','pid':'0020','host':'192.168.60.1:12346','goggles':'192.168.60.2:9003'}}
    if isinstance(diagnosis,dict):
        report['device_detail'] = {'collected':True,'matching_devices':min(100,len(diagnosis.get('DJIDevices',[]))) if isinstance(diagnosis.get('DJIDevices'),list) else 'unknown',
                                   'interfaces':min(100,len(diagnosis.get('interfaces',[]))) if isinstance(diagnosis.get('interfaces'),list) else 'unknown'}
    report['output'] = safe_output(output)
    now = time.monotonic()
    for name,stamp in (('packet_age_seconds',stats.get('last_packet_time')),('frame_age_seconds',last_frame)):
        report[name] = round(max(0,now-stamp),3) if type(stamp) in (int,float) and 0 < stamp <= now else 'unknown'
    return 'Goggles HDMI support report\n'+json.dumps(report,ensure_ascii=False,indent=2)
