//! Shared HTTP/WSS trust. Apple platforms verify through Security.framework;
//! iOS does not provide the Unix certificate files used by native-certs.
use rustls::pki_types::{pem::PemObject, CertificateDer};
use std::sync::Arc;

pub fn client(extra_pem: &str) -> Result<rustls::ClientConfig, &'static str> {
    let roots = CertificateDer::pem_slice_iter(extra_pem.as_bytes())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| "Invalid extra trust root")?;
    if !extra_pem.is_empty() && roots.is_empty() {
        return Err("Missing extra trust root");
    }
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let verifier =
        rustls_platform_verifier::Verifier::new_with_extra_roots(roots, provider.clone())
            .map_err(|_| "System certificate verifier unavailable")?;
    // Rustls names its custom-verifier entry point `dangerous`. The verifier
    // here performs platform chain/hostname/signature validation, never accept-all.
    Ok(rustls::ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(|_| "TLS protocols unavailable")?
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(verifier))
        .with_no_client_auth())
}

#[cfg(test)]
mod tests {
    #[test]
    fn public_trust_initializes_and_invalid_extra_roots_are_rejected() {
        assert!(super::client("").is_ok());
        assert!(super::client("not a certificate").is_err());
        assert!(
            super::client("-----BEGIN CERTIFICATE-----\nbroken\n-----END CERTIFICATE-----")
                .is_err()
        );
    }
}
