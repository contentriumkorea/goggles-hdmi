"""Native Security.framework generic-password storage; no shell arguments."""
import ctypes


class Keychain:
    SERVICE = b'com.contentrium.GogglesHDMI.authentication.v1'

    def __init__(self):
        self.security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        self.core = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.core.CFRelease.argtypes = [ctypes.c_void_p]
        self.security.SecKeychainFindGenericPassword.argtypes = [ctypes.c_void_p,ctypes.c_uint32,ctypes.c_char_p,
            ctypes.c_uint32,ctypes.c_char_p,ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_void_p)]
        self.security.SecKeychainAddGenericPassword.argtypes = [ctypes.c_void_p,ctypes.c_uint32,ctypes.c_char_p,
            ctypes.c_uint32,ctypes.c_char_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
        self.security.SecKeychainItemModifyAttributesAndData.argtypes = [ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p]
        self.security.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
        self.security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p,ctypes.c_void_p]

    def _find(self, reference):
        account = reference.encode('ascii')
        size,data,item = ctypes.c_uint32(),ctypes.c_void_p(),ctypes.c_void_p()
        status = self.security.SecKeychainFindGenericPassword(None,len(self.SERVICE),self.SERVICE,len(account),account,
            ctypes.byref(size),ctypes.byref(data),ctypes.byref(item))
        if status == -25300:  # errSecItemNotFound
            return None,None
        if status:
            raise OSError('Keychain access failed (%d)' % status)
        try:
            proof = ctypes.string_at(data,size.value)
        finally:
            self.security.SecKeychainItemFreeContent(None,data)
        return proof,item

    def get(self, reference):
        proof,item = self._find(reference)
        if item:
            self.core.CFRelease(item)
        return proof

    def set(self, reference, proof):
        _,item = self._find(reference)
        buffer = ctypes.create_string_buffer(proof)
        if item:
            try:
                status = self.security.SecKeychainItemModifyAttributesAndData(item,None,len(proof),buffer)
            finally:
                self.core.CFRelease(item)
        else:
            account = reference.encode('ascii')
            status = self.security.SecKeychainAddGenericPassword(None,len(self.SERVICE),self.SERVICE,len(account),account,len(proof),buffer,None)
        if status:
            raise OSError('Keychain save failed (%d)' % status)

    def delete(self, reference):
        _,item = self._find(reference)
        if item:
            try:
                status = self.security.SecKeychainItemDelete(item)
                if status:
                    raise OSError('Keychain delete failed (%d)' % status)
            finally:
                self.core.CFRelease(item)
